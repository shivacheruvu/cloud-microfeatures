"""Day 25 (Databricks): keep the Data Classification scope of the grocery catalog as code, with the Data
Classification API (GA 2026-10-14).

  python scan_scope.py --local        # reconcile the three policy versions against an in-memory API that follows the
                                      # documented rules; writes result.json
  python scan_scope.py --databricks   # the same against the real API on a scratch catalog; every step's outcome and
                                      # the config read back after each step must equal the local run

A small reconciler turns each policy version into the CatalogConfig the API expects (catalogs/<c>/config with
included_schemas or excluded_schemas), reads the current config, and plans one call: create (POST), update (PATCH
with an update_mask naming only the fields that change), or nothing. It also reports which columns that look like
personal data (email, phone, address, birth date, card digits) each version leaves unscanned.

The cloud run needs no compute (no SQL warehouse, no job): Unity Catalog and Data Classification are REST calls.
Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets); the hostname is never
printed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH_CATALOG = "microfeatures_day25"
SCHEME = "https"  # tests point the client at a local http server
API = "/api/data-classification/v1"
PII = re.compile(r"(email|phone|address|birth|postcode|card_last4)")


def catalog() -> dict:
    return json.loads((HERE / "data" / "catalog.json").read_text())


def policy() -> list[dict]:
    return json.loads((HERE / "data" / "policy.json").read_text())["versions"]


# ---------------------------------------------------------------- planning
def desired_config(version: dict, catalog_name: str) -> dict:
    cfg = {"name": f"catalogs/{catalog_name}/config"}
    if version.get("included_schemas") and version.get("excluded_schemas"):
        raise ValueError("included_schemas and excluded_schemas are mutually exclusive")
    for key in ("included_schemas", "excluded_schemas"):
        if version.get(key):
            cfg[key] = {"names": sorted(version[key])}
    return cfg


def scope(cfg: dict | None) -> dict | None:
    """The part of a CatalogConfig that decides what gets scanned, in a comparable form."""
    if cfg is None:
        return None
    out = {}
    for key in ("included_schemas", "excluded_schemas"):
        names = (cfg.get(key) or {}).get("names") or []
        if names:
            out[key] = sorted(names)
    return out


def plan(current: dict | None, desired: dict) -> tuple[str, list[str]]:
    """('create', []), ('update', <update_mask fields>) or ('noop', [])."""
    if current is None:
        return "create", []
    cur, want = scope(current), scope(desired)
    mask = [k for k in ("included_schemas", "excluded_schemas") if cur.get(k) != want.get(k)]
    return ("update", mask) if mask else ("noop", [])


def scanned_schemas(cfg: dict | None, schemas: list[str]) -> list[str]:
    s = scope(cfg)
    if s is None:
        return []
    if "included_schemas" in s:
        return [x for x in schemas if x in s["included_schemas"]]
    return [x for x in schemas if x not in s.get("excluded_schemas", [])]


def coverage(cfg: dict | None) -> dict:
    cat = catalog()["schemas"]
    scanned = scanned_schemas(cfg, sorted(cat))
    pii = [f"{s}.{t}.{c}" for s in sorted(cat) for t in sorted(cat[s]) for c in cat[s][t] if PII.search(c)]
    return {
        "schemas_scanned": scanned,
        "tables_scanned": sum(len(cat[s]) for s in scanned),
        "pii_like_columns_scanned": sum(p.split(".")[0] in scanned for p in pii),
        "pii_like_columns_missed": [p for p in pii if p.split(".")[0] not in scanned],
    }


# ---------------------------------------------------------------- the API, in memory
class LocalApi:
    """In-memory Data Classification API with the documented rules for the calls the reconciler makes: one config
    per catalog, create fails if one exists, update needs an existing config and an update_mask, delete disables."""

    def __init__(self):
        self.configs: dict[str, dict] = {}

    def get(self, name: str) -> tuple[int, dict | None]:
        return (200, json.loads(json.dumps(self.configs[name]))) if name in self.configs else (404, None)

    def create(self, parent: str, body: dict) -> tuple[int, dict | None]:
        name = f"{parent}/config"
        if name in self.configs:
            return 409, None
        if body.get("included_schemas") and body.get("excluded_schemas"):
            return 400, None
        self.configs[name] = {**body, "name": name}
        return 200, self.configs[name]

    def update(self, name: str, body: dict, mask: list[str]) -> tuple[int, dict | None]:
        if name not in self.configs:
            return 404, None
        if not mask:
            return 400, None
        new = dict(self.configs[name])
        for field in mask:
            if body.get(field):
                new[field] = body[field]
            else:
                new.pop(field, None)
        if new.get("included_schemas") and new.get("excluded_schemas"):
            return 400, None
        self.configs[name] = new
        return 200, new

    def delete(self, name: str) -> int:
        return 200 if self.configs.pop(name, None) is not None else 404


def outcome(status: int) -> str:
    if 200 <= status < 300:
        return "ok"
    if status == 404:
        return "absent"
    if status == 409:
        return "rejected (exists)"
    return f"error {status}"


def scenario(api, catalog_name: str) -> dict:
    """Apply the policy versions in order through `api` (LocalApi or RestApi) and record each step."""
    name, parent = f"catalogs/{catalog_name}/config", f"catalogs/{catalog_name}"
    steps = []

    def record(step: str, status: int, call: str):
        _, cfg = api.get(name)
        steps.append({"step": step, "call": call, "outcome": outcome(status), "scope_after": scope(cfg)})

    s, _ = api.get(name)
    record("read before any change", s, "GET")
    for i, version in enumerate(policy()):
        want = desired_config(version, catalog_name)
        _, current = api.get(name)
        action, mask = plan(current, want)
        if action == "create":
            s, _ = api.create(parent, want)
            record(f"apply {version['version']}", s, "POST")
            s, _ = api.create(parent, want)  # a second create must not overwrite
            record(f"create {version['version']} again", s, "POST")
            _, current = api.get(name)
            action, mask = plan(current, want)
            record(f"apply {version['version']} again", 200, "none" if action == "noop" else action)
        elif action == "update":
            s, _ = api.update(name, want, mask)
            record(f"apply {version['version']}", s, f"PATCH update_mask={','.join(mask)}")
        else:
            record(f"apply {version['version']}", 200, "none")
    s = api.delete(name)
    record("delete (disable classification)", s, "DELETE")
    return {"steps": steps}


def summarise(run: dict) -> dict:
    by_version = {}
    for v in policy():
        cfg = desired_config(v, "grocery")
        by_version[v["version"]] = {"why": v["why"], "scope": scope(cfg), **coverage(cfg)}
    calls = [s["call"] for s in run["steps"]]
    return {
        "steps": run["steps"],
        "write_calls": sum(c.split(" ")[0] in ("POST", "PATCH", "DELETE") for c in calls),
        "noop_reapplies": calls.count("none"),
        "versions": by_version,
    }


# ---------------------------------------------------------------- Databricks
class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code} {message}".strip())
        self.status, self.code = status, code


def _host() -> str:
    return os.environ["DATABRICKS_HOST"].rstrip("/").removeprefix("https://")


def _redact(text: str) -> str:
    return re.sub(r"\d{6,}", "<id>", text.replace(_host(), "<host>"))[:300]


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{SCHEME}://{_host()}{path}", method=method, data=data,
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, (json.loads(raw) if raw.strip() else {})
    except ValueError:
        return status, {}


def _uc(method: str, path: str, body: dict | None = None, ok=(200,)) -> dict:
    s, out = _request(method, path, body)
    if s not in ok:
        raise ApiError(s, out.get("error_code", ""), _redact(out.get("message", "")))
    return out


class RestApi:
    """The Data Classification API (v1) over REST. Non-2xx answers other than the ones the scenario expects
    (404 on read, 409 on a second create) raise, so a refusal is reported instead of compared."""

    def get(self, name):
        s, out = _request("GET", f"{API}/{name}")
        if s not in (200, 404):
            raise ApiError(s, out.get("error_code", ""), _redact(out.get("message", "")))
        return s, (out if s == 200 else None)

    def create(self, parent, body):
        s, out = _request("POST", f"{API}/{parent}/config", body)
        if s not in (200, 409):
            raise ApiError(s, out.get("error_code", ""), _redact(out.get("message", "")))
        return s, out

    def update(self, name, body, mask):
        q = urllib.parse.urlencode({"update_mask": ",".join(mask)})
        s, out = _request("PATCH", f"{API}/{name}?{q}", body)
        if s != 200:
            raise ApiError(s, out.get("error_code", ""), _redact(out.get("message", "")))
        return s, out

    def delete(self, name):
        s, out = _request("DELETE", f"{API}/{name}")
        if s not in (200, 404):
            raise ApiError(s, out.get("error_code", ""), _redact(out.get("message", "")))
        return s


class PrefixedApi:
    """Fallback when the workspace can't create a scratch catalog (Free Edition answered 400 INVALID_STATE on
    2026-10-29): the scenario runs on the workspace's default catalog, with the grocery schemas created as
    `mf25_<name>`. Every config sent covers only those scratch schemas: an included list is prefixed, and an
    excluded list also names every other schema in the catalog, so nothing that already existed is ever in scope.
    Configs read back are translated the other way, so steps compare equal to the local run."""

    def __init__(self, inner, prefix: str, ours: list[str], others: list[str]):
        self.inner, self.prefix, self.ours, self.others = inner, prefix, set(ours), sorted(others)

    def _out(self, cfg):
        if cfg is None:
            return None
        cfg = dict(cfg)
        for key in ("included_schemas", "excluded_schemas"):
            names = (cfg.get(key) or {}).get("names") or []
            mine = [n.removeprefix(self.prefix) for n in names if n in self.ours]
            if mine:
                cfg[key] = {"names": sorted(mine)}
            else:
                cfg.pop(key, None)
        return cfg

    def _in(self, body):
        body = dict(body)
        if body.get("included_schemas"):
            body["included_schemas"] = {"names": sorted(self.prefix + n for n in body["included_schemas"]["names"])}
        if body.get("excluded_schemas"):
            body["excluded_schemas"] = {"names": sorted([self.prefix + n for n in body["excluded_schemas"]["names"]]
                                                        + self.others)}
        return body

    def get(self, name):
        s, cfg = self.inner.get(name)
        return s, self._out(cfg)

    def create(self, parent, body):
        s, cfg = self.inner.create(parent, self._in(body))
        return s, self._out(cfg) if s == 200 else cfg

    def update(self, name, body, mask):
        s, cfg = self.inner.update(name, self._in(body), mask)
        return s, self._out(cfg)

    def delete(self, name):
        return self.inner.delete(name)


PREFIX = "mf25_"


def _run_on_default_catalog() -> dict | None:
    assignment = _uc("GET", "/api/2.1/unity-catalog/current-metastore-assignment")
    cat = assignment.get("default_catalog_name") or "workspace"
    api = RestApi()
    if api.get(f"catalogs/{cat}/config")[0] == 200:
        print("::warning title=Databricks day 25::The default catalog already has a Data Classification config, which "
              "the run never touches, so the scope reconciler was verified locally only")
        return None
    listed = _uc("GET", "/api/2.1/unity-catalog/schemas?" + urllib.parse.urlencode({"catalog_name": cat}))
    existing = [s["name"] for s in listed.get("schemas", [])]
    ours = [PREFIX + s for s in catalog()["schemas"]]
    if any(s in existing for s in ours):
        print("::warning title=Databricks day 25::mf25_ schemas already exist in the default catalog (left alone), so "
              "the scope reconciler was verified locally only")
        return None
    created = []
    try:
        for schema in ours:
            _uc("POST", "/api/2.1/unity-catalog/schemas",
                {"name": schema, "catalog_name": cat, "comment": "cloud-microfeatures day 25 scratch"})
            created.append(schema)
        return scenario(PrefixedApi(api, PREFIX, ours, [s for s in existing if s not in ours]), cat)
    finally:  # leave nothing behind: the config (only ever ours: there was none before) and the scratch schemas
        try:
            _request("DELETE", f"{API}/catalogs/{cat}/config")
            for schema in created:
                _uc("DELETE", f"/api/2.1/unity-catalog/schemas/{cat}.{schema}", ok=(200, 204, 404))
        except ApiError as e:
            print(f"::warning title=Databricks day 25 cleanup::dropping scratch schemas returned {e}")


def run_databricks() -> dict | None:
    """Scratch catalog with the five grocery schemas (empty: nothing to scan, nothing billed), the scenario, then
    the catalog is dropped. If the workspace can't create catalogs, fall back to prefixed scratch schemas in the
    default catalog (see PrefixedApi). Classification is never switched on for anything that existed before."""
    s, out = _request("POST", "/api/2.1/unity-catalog/catalogs",
                      {"name": SCRATCH_CATALOG, "comment": "cloud-microfeatures day 25 scratch (deleted by the run)"})
    if s not in (200, 409):
        print(f"::notice title=Databricks day 25::No scratch catalog ({s} {out.get('error_code', '')}); using "
              "prefixed scratch schemas in the default catalog instead")
        return _run_on_default_catalog()
    try:
        for schema in catalog()["schemas"]:
            _uc("POST", "/api/2.1/unity-catalog/schemas",
                {"name": schema, "catalog_name": SCRATCH_CATALOG}, ok=(200, 409))
        api = RestApi()
        if api.get(f"catalogs/{SCRATCH_CATALOG}/config")[0] == 200:  # leftover from an interrupted run
            api.delete(f"catalogs/{SCRATCH_CATALOG}/config")
        return scenario(api, SCRATCH_CATALOG)
    finally:  # leave nothing behind
        try:
            _request("DELETE", f"{API}/catalogs/{SCRATCH_CATALOG}/config")
            time.sleep(1)
            _uc("DELETE", f"/api/2.1/unity-catalog/catalogs/{SCRATCH_CATALOG}?force=true", ok=(200, 204, 404))
        except ApiError as e:
            print(f"::warning title=Databricks day 25 cleanup::dropping the scratch catalog returned {e}")


def main(mode: str) -> int:
    local = scenario(LocalApi(), SCRATCH_CATALOG)
    if mode != "--databricks":
        summary = summarise(local)
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "in-memory Data Classification API with the documented rules (local) / Data Classification "
                       "API v1 on a scratch catalog (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        return 0
    try:
        cloud = run_databricks()
    except ApiError as e:
        print(f"::error title=Databricks day 25 API call failed::{e}")
        return 1
    if cloud is None:
        return 0
    if cloud != local:
        diff = [(c, l) for c, l in zip(cloud["steps"], local["steps"]) if c != l][:3]
        print(f"::error title=Databricks day 25 differs from local::{json.dumps(diff)[:900]}")
        return 1
    s = summarise(cloud)
    v = s["versions"]
    print("::notice title=Databricks day 25::Data Classification API scope reconciler matched local on all "
          f"{len(s['steps'])} steps: {s['write_calls']} write calls (create, duplicate create, "
          f"2 masked PATCHes, delete), the duplicate create was rejected and the re-apply was a no-op. PII-like columns "
          f"left unscanned: v1 {len(v['v1']['pii_like_columns_missed'])}, v2 {len(v['v2']['pii_like_columns_missed'])}, "
          f"v3 {len(v['v3']['pii_like_columns_missed'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "--local"))
