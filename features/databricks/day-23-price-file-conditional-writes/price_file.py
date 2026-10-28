"""Day 23 (Databricks): two stores edit the same shelf-price file in a Unity Catalog volume without losing an edit,
using Files API 3.0 conditional operations (ETag + If-Match / If-None-Match).

  python price_file.py --local        # the scenario against an in-memory volume that follows the documented
                                      # Files API 3.0 rules; writes result.json
  python price_file.py --databricks   # the same scenario against a scratch managed volume over Files API 3.0;
                                      # every step's outcome and the final file must equal the local run

Scenario: HQ publishes shelf_prices.csv (create-only). Store east and store north both read it. East saves a
markdown first; north's save still carries the old ETag, so it is rejected instead of silently overwriting east's
markdown. North re-reads, re-applies its change and saves. A delete with a stale ETag is rejected too. The final
file has both edits; plain last-writer-wins would have lost east's.

Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). The hostname, workspace
ID and region are never printed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "microfeatures_day23"
VOLUME = "price_files"
FILE = "shelf_prices.csv"
SCHEME = "https"  # tests point the client at a local http server
EDITS = {"east": ("P01", "2.99"),   # oat milk markdown
         "north": ("P02", "4.49")}  # eggs price match


def seed() -> bytes:
    return (HERE / "data" / FILE).read_bytes()


def apply_edit(data: bytes, sku: str, price: str) -> bytes:
    rows = list(csv.DictReader(io.StringIO(data.decode())))
    if sku not in {r["sku"] for r in rows}:
        raise ValueError(sku)
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=["sku", "product", "price"], lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "price": price if r["sku"] == sku else r["price"]})
    return out.getvalue().encode()


def prices(data: bytes) -> dict[str, str]:
    return {r["sku"]: r["price"] for r in csv.DictReader(io.StringIO(data.decode()))}


def outcome(status: int) -> str:
    if 200 <= status < 300:
        return "ok"
    if status in (409, 412):  # 412 Precondition Failed; 409 FileAlreadyExists
        return "rejected"
    return f"error {status}"


class LocalVolume:
    """In-memory volume with the documented Files API 3.0 semantics for the calls the scenario makes."""

    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}
        self.n = 0

    def _etag(self, data: bytes) -> str:
        self.n += 1
        return '"' + hashlib.sha1(data + str(self.n).encode()).hexdigest() + '"'

    def put(self, path: str, data: bytes, if_match: str | None = None, if_none_match: str | None = None) -> tuple:
        cur = self.files.get(path)
        if if_none_match == "*" and cur is not None:
            return 412, None
        if if_match is not None and (cur is None or cur[1] != if_match):
            return 412, None
        etag = self._etag(data)
        self.files[path] = (data, etag)
        return 200, etag

    def get(self, path: str) -> tuple:
        cur = self.files.get(path)
        return (404, None, None) if cur is None else (200, cur[0], cur[1])

    def delete(self, path: str, if_match: str | None = None) -> int:
        cur = self.files.get(path)
        if if_match is not None and (cur is None or (if_match != "*" and cur[1] != if_match)):
            return 412 if cur is not None else 404
        self.files.pop(path, None)
        return 204


def scenario(vol, path: str) -> dict:
    """Run the two-store edit against `vol` (LocalVolume or FilesV3). Returns step outcomes and the final prices."""
    steps = []

    def step(name, status):
        steps.append({"step": name, "outcome": outcome(status)})

    s, _ = vol.put(path, seed(), if_none_match="*")
    step("hq publishes (If-None-Match: *)", s)
    s, _ = vol.put(path, seed(), if_none_match="*")
    step("hq publishes again (If-None-Match: *)", s)
    _, east_data, east_tag = vol.get(path)
    _, north_data, north_tag = vol.get(path)
    step("east and north read the same version", 200 if east_tag == north_tag and east_tag else 500)
    s, _ = vol.put(path, apply_edit(east_data, *EDITS["east"]), if_match=east_tag)
    step("east saves its markdown (If-Match)", s)
    s, _ = vol.put(path, apply_edit(north_data, *EDITS["north"]), if_match=north_tag)
    step("north saves with the stale ETag (If-Match)", s)
    stale_tag = north_tag
    _, north_data, north_tag = vol.get(path)
    s, _ = vol.put(path, apply_edit(north_data, *EDITS["north"]), if_match=north_tag)
    step("north re-reads and saves (If-Match)", s)
    step("delete with the stale ETag (If-Match)", vol.delete(path, if_match=stale_tag))
    _, final, final_tag = vol.get(path)
    step("cleanup delete with the current ETag", vol.delete(path, if_match=final_tag))
    return {"steps": steps, "final_prices": prices(final)}


def last_writer_wins() -> dict[str, str]:
    """What the file would hold if both stores just overwrote it from their first read."""
    return prices(apply_edit(seed(), *EDITS["north"]))  # north saves last, over east's markdown


def summarise(run: dict) -> dict:
    before = prices(seed())
    lww = last_writer_wins()
    return {
        "steps": run["steps"],
        "rejected_writes": sum(s["outcome"] == "rejected" for s in run["steps"]),
        "final_prices": run["final_prices"],
        "edits_kept": sorted(k for k, (sku, p) in EDITS.items() if run["final_prices"].get(sku) == p),
        "last_writer_wins_would_lose": sorted(k for k, (sku, p) in EDITS.items() if lww.get(sku) != p),
        "changed_skus": sorted(k for k in before if run["final_prices"].get(k) != before[k]),
    }


# ---------------------------------------------------------------- Databricks
class ApiError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(f"{status} {code}")
        self.status, self.code = status, code


def _host() -> str:
    return os.environ["DATABRICKS_HOST"].rstrip("/").removeprefix("https://")


def _request(host: str, method: str, path: str, data: bytes | None = None, headers: dict | None = None):
    h = {"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}", **(headers or {})}
    req = urllib.request.Request(f"{SCHEME}://{host}{path}", method=method, data=data, headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def _uc(method: str, path: str, body: dict | None = None, ok=(200,)) -> tuple[dict, object]:
    data = json.dumps(body).encode() if body is not None else None
    s, raw, headers = _request(_host(), method, path, data, {"Content-Type": "application/json"})
    if s not in ok:
        try:
            code = json.loads(raw).get("error_code", "")
        except ValueError:
            code = ""
        raise ApiError(s, code)
    return (json.loads(raw) if raw.strip() else {}), headers


def files_host() -> str:
    """Files API 3.0 lives on <workspace-id>.storage.<region>.<domain>: ID from the X-Databricks-Org-Id response
    header, region from the metastore summary, domain from the workspace hostname."""
    summary, headers = _uc("GET", "/api/2.1/unity-catalog/metastore_summary")
    org = headers.get("X-Databricks-Org-Id") or ""
    region = summary.get("region") or ""
    domain = _host().split(".", 1)[1] if "." in _host() else ""
    if not (org.isdigit() and region and domain):
        raise ApiError(0, "could not work out the Files API 3.0 hostname")
    return f"{org}.storage.{region}.{domain}"


class FilesV3:
    def __init__(self, host: str):
        self.host = host

    def _path(self, path: str) -> str:
        return "/api/3.0/fs/files" + urllib.parse.quote(path)

    def put(self, path, data, if_match=None, if_none_match=None):
        h = {"Content-Type": "text/csv", "Content-Length": str(len(data))}
        if if_match:
            h["If-Match"] = if_match
        if if_none_match:
            h["If-None-Match"] = if_none_match
        s, _, headers = _request(self.host, "PUT", self._path(path), data, h)
        return s, headers.get("ETag")

    def get(self, path):
        s, raw, headers = _request(self.host, "GET", self._path(path))
        return (s, raw, headers.get("ETag")) if s == 200 else (s, None, None)

    def delete(self, path, if_match=None):
        s, _, _ = _request(self.host, "DELETE", self._path(path), headers={"If-Match": if_match} if if_match else {})
        return s


def run_databricks() -> dict | None:
    assignment, _ = _uc("GET", "/api/2.1/unity-catalog/current-metastore-assignment")
    catalog = assignment.get("default_catalog_name") or "workspace"
    host = files_host()
    try:
        socket.getaddrinfo(host, 443)
    except OSError:
        print("::warning title=Databricks day 23::The Files API 3.0 regional hostname doesn't resolve for this "
              "workspace, so the scenario was verified locally only")
        return None
    schema, volume = f"{catalog}.{SCHEMA}", f"{catalog}.{SCHEMA}.{VOLUME}"
    _uc("POST", "/api/2.1/unity-catalog/schemas", {"name": SCHEMA, "catalog_name": catalog,
                                                    "comment": "cloud-microfeatures day 23 scratch"}, ok=(200, 409))
    try:
        _uc("POST", "/api/2.1/unity-catalog/volumes", {"catalog_name": catalog, "schema_name": SCHEMA,
                                                        "name": VOLUME, "volume_type": "MANAGED"}, ok=(200, 409))
        path = f"/Volumes/{catalog}/{SCHEMA}/{VOLUME}/{FILE}"
        vol = FilesV3(host)
        if _endpoint_missing(vol, path):
            print("::warning title=Databricks day 23::Files API 3.0 isn't available in this workspace yet (a "
                  "workspace admin turns on 'Files API V3 with Large File Upload Support' on the Previews page), so "
                  "the scenario was verified locally only")
            return None
        vol.delete(path)  # leftover from an interrupted run; 204 whether or not it exists
        return scenario(vol, path)
    finally:  # leave nothing behind
        for p in (f"/api/2.1/unity-catalog/volumes/{volume}", f"/api/2.1/unity-catalog/schemas/{schema}?force=true"):
            try:
                _uc("DELETE", p, ok=(200, 204, 404))
            except ApiError as e:
                print(f"::warning title=Databricks day 23 cleanup::DELETE {p.split('/')[4]} returned {e}")
            time.sleep(1)


def _endpoint_missing(vol: FilesV3, path: str) -> bool:
    """Probe with a GET of the not-yet-written file: 401/403, or a 404 that isn't a file-not-found, means the
    Files API 3.0 endpoint isn't on for this workspace (preview off)."""
    s, raw, _ = _request(vol.host, "GET", vol._path(path))
    try:
        code = json.loads(raw).get("error_code", "") if raw.strip() else ""
    except ValueError:
        code = ""
    return s in (401, 403) or (s == 404 and code not in ("", "NOT_FOUND", "RESOURCE_DOES_NOT_EXIST",
                                                          "FILE_NOT_FOUND"))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    local = scenario(LocalVolume(), f"/Volumes/workspace/{SCHEMA}/{VOLUME}/{FILE}")
    if mode == "--databricks":
        try:
            cloud = run_databricks()
        except ApiError as e:
            print(f"::error title=Databricks day 23 API call failed::{e}")
            raise SystemExit(1)
        if cloud is None:
            raise SystemExit(0)
        if cloud != local:
            diff = [(c, l) for c, l in zip(cloud["steps"], local["steps"]) if c != l][:4]
            print(f"::error title=Databricks day 23 differs from local::{json.dumps(diff)[:700]} final "
                  f"{json.dumps(cloud['final_prices'])}")
            raise SystemExit(1)
        s = summarise(cloud)
        print("::notice title=Databricks day 23::Files API 3.0 conditional writes on a Unity Catalog volume matched "
              f"local: {s['rejected_writes']} stale or duplicate writes rejected, both store edits kept "
              f"({json.dumps(s['final_prices'])}); last-writer-wins would have lost {s['last_writer_wins_would_lose']}")
    else:
        summary = summarise(local)
        (HERE / "result.json").write_text(json.dumps({"engine": "in-memory volume with Files API 3.0 rules (local) / "
                                                      "Files API 3.0 on a Unity Catalog volume (cloud)", **summary},
                                                     indent=2) + "\n")
        print(json.dumps(summary, indent=2))
