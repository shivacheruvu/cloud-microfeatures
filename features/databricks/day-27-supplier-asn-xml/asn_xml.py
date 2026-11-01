"""Day 27 (Databricks): read supplier delivery notes (ASN XML) with native XML support, explicit schema and a rescued
data column, so lines that don't fit the schema are kept instead of silently becoming NULL.

  python asn_xml.py --local        # pure-Python reference with the same rules as read_files(format => 'xml') with
                                   # an explicit schema and rescuedDataColumn; writes result.json
  python asn_xml.py --databricks   # uploads data/*.xml to a scratch Unity Catalog volume (Files API), runs
                                   # receipts.sql on a serverless SQL warehouse (Statement Execution API), compares
                                   # every line with the local run, deletes the volume and schema

Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). The hostname and long digit
runs (organisation and workspace IDs) are redacted from anything printed.
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
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "microfeatures_day27"
VOLUME = "asn"
SCHEME = "https"
ROW_TAG = "line"
# The explicit schema in receipts.sql, as (column, type). Attributes get the default "_" prefix.
COLUMNS = [("_no", "INT"), ("sku", "STRING"), ("qty", "INT"), ("unit_cost", "DECIMAL(8,2)")]
INT_RE = re.compile(r"^[+-]?\d+$")
DEC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")


def files() -> list[Path]:
    return sorted((HERE / "data").glob("*.xml"))


def _cast(value: str, typ: str):
    """Cast like Spark's XML reader for these types; None means the value doesn't fit (it goes to _rescued)."""
    if typ == "STRING":
        return value
    if typ == "INT":
        return int(value) if INT_RE.match(value) and -2**31 <= int(value) < 2**31 else None
    m = re.match(r"DECIMAL\((\d+),(\d+)\)", typ)
    if m and DEC_RE.match(value):
        precision, scale = int(m.group(1)), int(m.group(2))
        try:
            d = Decimal(value).quantize(Decimal(1).scaleb(-scale))
        except InvalidOperation:
            return None
        return str(d) if len(d.as_tuple().digits) <= precision else None
    return None


def parse_file(name: str, text: str) -> list[dict]:
    """One row per <line>: schema columns cast, values that don't fit or fields not in the schema go to _rescued."""
    types = dict(COLUMNS)
    rows = []
    for el in ET.fromstring(text).iter(ROW_TAG):
        fields = {f"_{k}": v for k, v in el.attrib.items()}
        fields.update({c.tag: (c.text or "").strip() for c in el})
        row, rescued = {"file": name}, {}
        for col, typ in COLUMNS:
            raw = fields.get(col)
            val = None if raw is None else _cast(raw, typ)
            if raw is not None and val is None:
                rescued[col] = raw
            row[col] = val
        rescued.update({k: v for k, v in fields.items() if k not in types})
        row["rescued"] = rescued or None
        rows.append(row)
    return rows


def run_local() -> list[dict]:
    out = []
    for f in files():
        out.extend(parse_file(f.name, f.read_text()))
    return sorted(out, key=lambda r: (r["file"], r["_no"]))


def deliveries() -> list[dict]:
    out = []
    for f in files():
        root = ET.fromstring(f.read_text())
        out.append({"file": f.name, "id": root.get("id"), "supplier": root.get("supplier")})
    return out


def summarise(rows: list[dict]) -> dict:
    by_file = {d["file"]: d for d in deliveries()}
    suppliers = []
    for f, d in by_file.items():
        mine = [r for r in rows if r["file"] == f]
        priced = [r for r in mine if r["qty"] is not None and r["unit_cost"] is not None]
        suppliers.append({"supplier": d["supplier"], "delivery": d["id"], "lines": len(mine),
                          "priced_lines": len(priced), "units": sum(r["qty"] for r in priced),
                          "value": str(sum((r["qty"] * Decimal(r["unit_cost"]) for r in priced), Decimal("0.00"))),
                          "rescued_lines": sum(1 for r in mine if r["rescued"])})
    rescued = [{"delivery": by_file[r["file"]]["id"], "line": r["_no"], "sku": r["sku"], "rescued": r["rescued"]}
               for r in rows if r["rescued"]]
    return {"files": len(by_file), "lines": len(rows), "rescued_lines": len(rescued),
            "null_without_rescue": sum(1 for r in rows if r["qty"] is None or r["unit_cost"] is None),
            "suppliers": suppliers, "rescued": rescued}


def query_sql(path: str) -> str:
    if not re.fullmatch(r"/Volumes/[\w-]+/[\w-]+/[\w-]+/", path):
        raise ValueError("volume folder path expected")
    body = (HERE / "receipts.sql").read_text().split("\n", 1)[1]
    return body.replace("{path}", path).strip()


def normalise_cloud(data_array: list[list]) -> list[dict]:
    """Statement API rows (all strings) into the local row shape. The rescued JSON also carries _file_path."""
    out = []
    for file, no, sku, qty, cost, rescued in data_array:
        r = json.loads(rescued) if rescued else {}
        r.pop("_file_path", None)
        out.append({"file": file, "_no": int(no) if no is not None else None, "sku": sku,
                    "qty": int(qty) if qty is not None else None,
                    "unit_cost": str(Decimal(cost).quantize(Decimal("0.01"))) if cost is not None else None,
                    "rescued": {k: str(v) for k, v in r.items()} or None})
    return sorted(out, key=lambda r: (r["file"], r["_no"] or 0))


# ---------------------------------------------------------------- Databricks
class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code} {message}".strip())
        self.status, self.code, self.message = status, code, message


class ComputeRefused(Exception):
    """The workspace refused compute (warehouse or account state), not a defect in this feature."""


def _host() -> str:
    return os.environ["DATABRICKS_HOST"].rstrip("/").removeprefix("https://")


def redact(text: str, host: str = "") -> str:
    text = str(text)
    if host:
        text = text.replace(host, "<host>")
    return re.sub(r"\d{6,}", "<id>", " ".join(text.split()))[:300]


def _request(method: str, path: str, body=None, raw: bytes | None = None) -> tuple[int, dict]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
               "Content-Type": "application/octet-stream" if raw is not None else "application/json"}
    req = urllib.request.Request(f"{SCHEME}://{_host()}{path}", method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        out, status = e.read(), e.code
    try:
        return status, (json.loads(out) if out.strip() else {})
    except ValueError:
        return status, {}


def _call(method, path, body=None, ok=(200,), raw=None) -> dict:
    s, out = _request(method, path, body, raw)
    if s not in ok:
        raise ApiError(s, out.get("error_code", ""), redact(out.get("message", ""), _host()))
    return out


def compute_refused(status: int, code: str, message: str) -> bool:
    """The refusals seen since 2026-10-24 on the Free Edition workspace: the warehouse answering 400 'could not be
    processed by the warehouse', FEATURE_DISABLED, and 403 'Organization ... has been cancelled or is not active'."""
    m = message.lower()
    return (code == "FEATURE_DISABLED" or "feature_disabled" in m or "not active" in m or "cancelled" in m
            or (status == 400 and "could not be processed by the warehouse" in m))


def _warehouse() -> str:
    whs = _call("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
    if not whs:
        raise ComputeRefused("no SQL warehouse in the workspace")
    return next((w for w in whs if w.get("enable_serverless_compute")), whs[0])["id"]


def statement(wh: str, sql: str) -> list[list]:
    s, res = _request("POST", "/api/2.0/sql/statements",
                      {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    if s != 200:
        code, msg = res.get("error_code", ""), res.get("message", "")
        if compute_refused(s, code, msg):
            raise ComputeRefused(redact(f"{s} {code} {msg}", _host()))
        raise ApiError(s, code, redact(msg, _host()))
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _call("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        err = res["status"].get("error", {})
        if compute_refused(400, err.get("error_code", ""), err.get("message", "")):
            raise ComputeRefused(redact(err.get("message", ""), _host()))
        raise ApiError(400, res["status"]["state"], redact(err.get("message", ""), _host()))
    rows = list(res["result"].get("data_array", []) or [])
    chunk = res["result"].get("next_chunk_internal_link")
    while chunk:
        nxt = _call("GET", chunk)
        rows.extend(nxt.get("data_array", []) or [])
        chunk = nxt.get("next_chunk_internal_link")
    return rows


def run_databricks() -> list[dict]:
    cat = _call("GET", "/api/2.1/unity-catalog/current-metastore-assignment").get("default_catalog_name") or "workspace"
    s, _ = _request("POST", "/api/2.1/unity-catalog/schemas",
                    {"name": SCHEMA, "catalog_name": cat, "comment": "cloud-microfeatures day 27 scratch"})
    if s not in (200, 409):  # 409: left by an interrupted run of this feature; it is ours and is dropped below
        raise ApiError(s, "SCHEMA_CREATE", "could not create the scratch schema")
    try:
        _call("POST", "/api/2.1/unity-catalog/volumes",
              {"catalog_name": cat, "schema_name": SCHEMA, "name": VOLUME, "volume_type": "MANAGED"}, ok=(200, 409))
        folder = f"/Volumes/{cat}/{SCHEMA}/{VOLUME}/"
        for f in files():
            _call("PUT", "/api/2.0/fs/files" + urllib.parse.quote(folder + f.name) + "?overwrite=true",
                  raw=f.read_bytes(), ok=(200, 201, 204))
        return normalise_cloud(statement(_warehouse(), query_sql(folder)))
    finally:  # leave nothing behind
        try:
            _request("DELETE", f"/api/2.1/unity-catalog/volumes/{cat}.{SCHEMA}.{VOLUME}")
            _call("DELETE", f"/api/2.1/unity-catalog/schemas/{cat}.{SCHEMA}?force=true", ok=(200, 204, 404))
        except ApiError as e:
            print(f"::warning title=Databricks day 27 cleanup::dropping the scratch schema returned {e}")


def compare(cloud: list[dict], local: list[dict]) -> int:
    if cloud != local:
        diff = [(c, l) for c, l in zip(cloud, local) if c != l][:2] or [len(cloud), len(local)]
        print(f"::error title=Databricks day 27 differs from local::{json.dumps(diff)[:900]}")
        return 1
    s = summarise(cloud)
    sup = "; ".join(f"{x['supplier']} {x['priced_lines']}/{x['lines']} priced, {x['value']}" for x in s["suppliers"])
    res = "; ".join(f"{r['delivery']} line {r['line']} {sorted(r['rescued'])}" for r in s["rescued"])
    print(f"::notice title=Databricks day 27::native XML read_files matched local on all {s['lines']} lines from "
          f"{s['files']} delivery notes. {s['rescued_lines']} lines kept their misfit values in _rescued ({res}). {sup}")
    return 0


def main(mode: str) -> int:
    local = run_local()
    if mode != "--databricks":
        s = summarise(local)
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "Python reference of read_files(format => 'xml') with explicit schema + rescuedDataColumn "
                       "(local) / Databricks serverless SQL warehouse (cloud)", **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
        return 0
    try:
        cloud = run_databricks()
    except ComputeRefused as e:
        print(f"::warning title=Databricks day 27::the workspace refused compute ({e}); the account looks inactive, "
              "so native XML reading was verified locally only (files uploaded and deleted, nothing left behind)")
        return 0
    except ApiError as e:
        if compute_refused(e.status, e.code, e.message):
            print(f"::warning title=Databricks day 27::the workspace refused the call ({e}); verified locally only")
            return 0
        print(f"::error title=Databricks day 27 API call failed::{e}")
        return 1
    return compare(cloud, local)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "--local"))
