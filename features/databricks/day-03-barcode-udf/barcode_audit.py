"""Day 3 (Databricks): audit grocery scan barcodes with a Unity Catalog Python UDF (named HANDLER, environment 6).

  python barcode_audit.py --local        # DuckDB, running the UDF's own Python body as a DuckDB function; writes result.json
  python barcode_audit.py --databricks   # serverless SQL warehouse via the Statement Execution API:
                                         # create schema + UDF, run the audit, compare with local, drop the UDF

Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed
about the workspace except the query results.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "microfeatures_day03"
KEYS = ["lane", "scans", "valid", "bad_check_digit", "bad_length", "not_numeric", "distinct_bad_codes"]


def load_rows() -> list[dict]:
    with open(HERE / "data" / "scans.csv", newline="") as f:
        return list(csv.DictReader(f))


def udf_sql(schema: str = SCHEMA) -> str:
    return (HERE / "gtin_status.sql").read_text().replace("{schema}", schema)


def udf_handler():
    """The exact Python body between $$ ... $$ in gtin_status.sql, loaded the way the UDF runtime loads it."""
    sql = (HERE / "gtin_status.sql").read_text()
    body = re.search(r"AS \$\$\n(.*)\$\$", sql, re.S)[1]
    handler = re.search(r"HANDLER '(\w+)'", sql)[1]
    scope: dict = {}
    exec(compile(body, "gtin_status.sql", "exec"), scope)  # noqa: S102 - our own checked-in UDF body
    return scope[handler]


def audit_sql(rows: list[dict], schema: str = SCHEMA) -> str:
    def lit(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"
    values = ",\n".join(f"    ({lit(r['scanned_at'])}, {lit(r['lane'])}, {lit(r['barcode'])})" for r in rows)
    return (HERE / "scan_audit.sql").read_text().replace("{values}", values).replace("{schema}", schema)


def normalise(raw: list[list]) -> list[dict]:
    return [{k: (v if k == "lane" else int(v)) for k, v in zip(KEYS, r)} for r in raw]


def run_local(rows: list[dict]) -> list[dict]:
    import duckdb
    con = duckdb.connect()
    con.create_function("gtin_status", udf_handler(), ["VARCHAR"], "VARCHAR")
    return normalise(con.sql(audit_sql(rows).replace(f"{SCHEMA}.gtin_status", "gtin_status")).fetchall())


def _api(method: str, path: str, body: dict | None = None) -> dict:
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    req = urllib.request.Request(host + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        print(f"::error title=Databricks API {e.code}::{method} {path.split('?')[0]}: {e.read()[:300]!r}")
        raise SystemExit(1)


def _warehouse() -> str:
    warehouses = _api("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
    if not warehouses:
        print("::error title=Databricks day 3::No SQL warehouse in the workspace")
        raise SystemExit(1)
    return next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])["id"]


def statement(wh: str, sql: str, name: str, fail: bool = True) -> dict | None:
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")[:500].replace("\n", " ")
        print(f"::{'error' if fail else 'warning'} title=Databricks day 3 {name} {res['status']['state']}::{msg}")
        if fail:
            raise SystemExit(1)
        return None
    return res


def run_databricks(rows: list[dict]) -> list[dict]:
    wh = _warehouse()
    statement(wh, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}", "create schema")
    try:
        statement(wh, udf_sql(), "create UDF")
        res = statement(wh, audit_sql(rows), "audit query")
        return normalise(res["result"]["data_array"])
    finally:  # leave nothing behind
        statement(wh, f"DROP FUNCTION IF EXISTS {SCHEMA}.gtin_status", "drop UDF", fail=False)
        statement(wh, f"DROP SCHEMA IF EXISTS {SCHEMA}", "drop schema", fail=False)


def summarise(lanes: list[dict]) -> dict:
    scans = sum(r["scans"] for r in lanes)
    bad = scans - sum(r["valid"] for r in lanes)
    worst = max(lanes, key=lambda r: (r["scans"] - r["valid"]) / r["scans"])
    return {
        "scans": scans,
        "invalid_scans": bad,
        "invalid_pct": round(100 * bad / scans, 1),
        "worst_lane": worst["lane"],
        "worst_lane_invalid_pct": round(100 * (worst["scans"] - worst["valid"]) / worst["scans"], 1),
        "by_status": {k: sum(r[k] for r in lanes) for k in ["valid", "bad_check_digit", "bad_length", "not_numeric"]},
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    rows = load_rows()
    local = run_local(rows)
    if mode == "--databricks":
        cloud = run_databricks(rows)
        for r in cloud:
            print(r)
        if cloud != local:
            diff = [(c, l) for c, l in zip(cloud, local) if c != l] or [("rows", len(cloud), len(local))]
            print(f"::error title=Databricks result differs from local::{json.dumps(diff)[:900]}")
            raise SystemExit(1)
        print("::notice title=Databricks day 3::Python UDF (HANDLER, environment 6) ran on a serverless SQL warehouse "
              f"and matched local: {json.dumps(summarise(cloud))}")
    else:
        summary = summarise(local)
        (HERE / "result.json").write_text(json.dumps({"engine": "duckdb + same Python body (local) / Databricks SQL "
                                                      "Unity Catalog Python UDF (cloud)", **summary,
                                                      "by_lane": local}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
