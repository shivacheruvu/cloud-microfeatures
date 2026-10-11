"""Day 6 (Databricks): re-load a corrected grocery scan export idempotently with INSERT INTO ... REPLACE USING and
REPLACE ON, and show where they differ: an undated (NULL scan_date) adjustment row.

  python reload.py --local        # DuckDB: the same delete-then-insert semantics in one transaction; writes result.json
  python reload.py --databricks   # serverless SQL warehouse via the Statement Execution API: scratch tables, run each
                                  # statement twice, snapshot after every run, compare with local, drop everything

Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed
about the workspace except the query results.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "microfeatures_day06"
COLS = ["store", "scan_date", "barcode", "units", "amount_cents"]
SNAP_KEYS = ["store", "scan_date", "lines", "units", "amount_cents"]
# (snapshot name, target table, statement): each statement runs twice on its own copy of the base table
RUNS = [("using_once", "sales_using", "replace_using"), ("using_twice", "sales_using", "replace_using"),
        ("on_once", "sales_on", "replace_on"), ("on_twice", "sales_on", "replace_on")]


def load(name: str) -> list[dict]:
    with open(HERE / "data" / f"{name}.csv", newline="") as f:
        return [{**r, "scan_date": r["scan_date"] or None, "units": int(r["units"]),
                 "amount_cents": int(r["amount_cents"])} for r in csv.DictReader(f)]


def statements(schema: str = SCHEMA, target: str = "{target}") -> dict[str, str]:
    """{'replace_using': sql, 'replace_on': sql, 'snapshot': sql} from reload.sql, comments removed."""
    out = {}
    for chunk in (HERE / "reload.sql").read_text().split(";"):
        lines = chunk.strip().splitlines()
        if not lines:
            continue
        name = lines[0].split(":", 1)[1].strip()
        body = "\n".join(l for l in lines if not l.lstrip().startswith("--"))
        out[name] = body.replace("{schema}", schema).replace("{target}", target)
    return out


def table_sql(name: str, rows: list[dict], schema: str = SCHEMA) -> str:
    def lit(v):
        return "NULL" if v is None else (str(v) if isinstance(v, int) else f"'{v}'")
    values = ",\n".join("  (" + ", ".join(lit(r[c]) for c in COLS) + ")" for r in rows)
    return (f"CREATE OR REPLACE TABLE {schema}.{name} AS\n"
            f"SELECT CAST(store AS STRING) AS store, CAST(scan_date AS DATE) AS scan_date,\n"
            f"       CAST(barcode AS STRING) AS barcode, CAST(units AS INT) AS units,\n"
            f"       CAST(amount_cents AS INT) AS amount_cents FROM VALUES\n"
            f"{values}\nAS t({', '.join(COLS)})")


def normalise(raw: list[list]) -> list[dict]:
    return [{k: (v if k in ("store", "scan_date") else int(float(v))) for k, v in zip(SNAP_KEYS, r)} for r in raw]


# ---------- local: DuckDB has no REPLACE USING / ON, so run the documented semantics (atomic delete, then insert)
LOCAL_DML = {
    "replace_using": "DELETE FROM {target} WHERE EXISTS (SELECT 1 FROM correction s "
                     "WHERE {target}.store = s.store AND {target}.scan_date = s.scan_date)",
    "replace_on": "DELETE FROM {target} WHERE EXISTS (SELECT 1 FROM correction s "
                  "WHERE {target}.store IS NOT DISTINCT FROM s.store "
                  "AND {target}.scan_date IS NOT DISTINCT FROM s.scan_date)",
}


def run_local(base: list[dict], correction: list[dict]) -> dict[str, list[dict]]:
    import duckdb
    con = duckdb.connect()
    ddl = "(store VARCHAR, scan_date DATE, barcode VARCHAR, units INTEGER, amount_cents INTEGER)"
    for name, rows in (("sales_base", base), ("correction", correction)):
        con.execute(f"CREATE TABLE {name} {ddl}")
        con.executemany(f"INSERT INTO {name} VALUES (?, ?, ?, ?, ?)", [[r[c] for c in COLS] for r in rows])
    snap = statements(schema="main")["snapshot"]
    out = {"base": normalise(con.sql(snap.replace("{target}", "sales_base")).fetchall())}
    for target in ("sales_using", "sales_on"):
        con.execute(f"CREATE TABLE {target} AS SELECT * FROM sales_base")
    for snap_name, target, stmt in RUNS:
        con.execute("BEGIN")
        con.execute(LOCAL_DML[stmt].replace("{target}", target))
        con.execute(f"INSERT INTO {target} SELECT * FROM correction")
        con.execute("COMMIT")
        out[snap_name] = normalise(con.sql(snap.replace("{target}", target)).fetchall())
    return out


# ---------- Databricks
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
        print("::error title=Databricks day 6::No SQL warehouse in the workspace")
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
        print(f"::{'error' if fail else 'warning'} title=Databricks day 6 {name} {res['status']['state']}::{msg}")
        if fail:
            raise SystemExit(1)
        return None
    return res


def run_databricks(base: list[dict], correction: list[dict]) -> dict[str, list[dict]]:
    wh = _warehouse()
    tables = ("sales_base", "correction", "sales_using", "sales_on")
    statement(wh, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}", "create schema")
    try:
        statement(wh, table_sql("sales_base", base), "create sales_base")
        statement(wh, table_sql("correction", correction), "create correction")
        snap = lambda target, name: normalise(  # noqa: E731
            statement(wh, statements(target=target)["snapshot"], name)["result"].get("data_array", []))
        out = {"base": snap("sales_base", "snapshot base")}
        for target in ("sales_using", "sales_on"):
            statement(wh, f"CREATE OR REPLACE TABLE {SCHEMA}.{target} AS SELECT * FROM {SCHEMA}.sales_base",
                      f"create {target}")
        for snap_name, target, stmt in RUNS:
            statement(wh, statements(target=target)[stmt], f"{stmt} ({snap_name})")
            out[snap_name] = snap(target, f"snapshot {snap_name}")
        return out
    finally:  # leave nothing behind
        for t in tables:
            statement(wh, f"DROP TABLE IF EXISTS {SCHEMA}.{t}", f"drop {t}", fail=False)
        statement(wh, f"DROP SCHEMA IF EXISTS {SCHEMA}", "drop schema", fail=False)


# ---------- summary
def _totals(snap: list[dict]) -> dict:
    undated = [r for r in snap if r["scan_date"] is None]
    return {"lines": sum(r["lines"] for r in snap), "units": sum(r["units"] for r in snap),
            "undated_rows": sum(r["lines"] for r in undated)}


def summarise(res: dict[str, list[dict]]) -> dict:
    day = lambda snap: next(r for r in snap if r["store"] == "north" and r["scan_date"] == "2026-10-02")  # noqa: E731
    return {
        "corrected_day": {"store": "north", "scan_date": "2026-10-02",
                          "before": {k: day(res["base"])[k] for k in ("lines", "units", "amount_cents")},
                          "after": {k: day(res["on_once"])[k] for k in ("lines", "units", "amount_cents")}},
        "totals": {k: _totals(v) for k, v in res.items()},
        "replace_using_idempotent": res["using_once"] == res["using_twice"],
        "replace_on_idempotent": res["on_once"] == res["on_twice"],
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    base, correction = load("sales_base"), load("correction")
    local = run_local(base, correction)
    if mode == "--databricks":
        cloud = run_databricks(base, correction)
        if cloud != local:
            diff = [(n, c, l) for n in local for c, l in zip(cloud.get(n, []), local[n]) if c != l][:6] \
                or [(n, len(cloud.get(n, [])), len(local[n])) for n in local]
            print(f"::error title=Databricks result differs from local::{json.dumps(diff)[:900]}")
            raise SystemExit(1)
        print("::notice title=Databricks day 6::INSERT INTO ... REPLACE USING and REPLACE ON each ran twice on a "
              "serverless SQL warehouse and every snapshot matched local: "
              f"{json.dumps(summarise(cloud))[:700]}")
    else:
        summary = summarise(local)
        (HERE / "result.json").write_text(json.dumps({"engine": "duckdb delete+insert transaction (local) / "
                                                      "Databricks SQL INSERT INTO ... REPLACE USING / REPLACE ON "
                                                      "(cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
