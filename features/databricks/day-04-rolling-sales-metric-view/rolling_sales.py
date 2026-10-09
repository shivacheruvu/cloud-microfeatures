"""Day 4 (Databricks): rolling and month-to-date grocery sales from a Unity Catalog metric view with window measures.

  python rolling_sales.py --local        # DuckDB: the same measures written by hand with window functions; writes result.json
  python rolling_sales.py --databricks   # serverless SQL warehouse via the Statement Execution API: create a scratch
                                         # table + metric view, query its window measures, compare with local, drop all

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
SCHEMA = "microfeatures_day04"
KEYS = ["store", "sale_date", "units_sold", "units_prior_7d", "open_days_prior_7d", "units_mtd"]
SPIKE = 1.4  # a day is a spike when it sells 40% more than its prior-7-day average per trading day


def load_rows() -> list[dict]:
    with open(HERE / "data" / "daily_units.csv", newline="") as f:
        return list(csv.DictReader(f))


def table_sql(rows: list[dict], schema: str = SCHEMA) -> str:
    values = ",\n".join(f"  ('{r['sale_date']}', '{r['store']}', {int(r['units'])})" for r in rows)
    return (f"CREATE OR REPLACE TABLE {schema}.daily_units AS\n"
            f"SELECT CAST(sale_date AS DATE) AS sale_date, store, CAST(units AS INT) AS units FROM VALUES\n"
            f"{values}\nAS t(sale_date, store, units)")


def metric_view_sql(schema: str = SCHEMA) -> str:
    return (HERE / "metric_view.sql").read_text().replace("{schema}", schema)


def queries(schema: str = SCHEMA) -> dict[str, str]:
    """{'by_store': sql, 'all_stores': sql} from queries.sql (each statement follows a '-- name:' comment)."""
    out = {}
    for chunk in (HERE / "queries.sql").read_text().split(";"):
        chunk = chunk.strip()
        if chunk:
            name = chunk.split(":", 1)[0].removeprefix("--").strip()
            out[name] = chunk.replace("{schema}", schema)
    return out


def _num(v):
    return None if v is None else int(float(v))


def normalise(raw: list[list]) -> list[dict]:
    return [{k: (str(v)[:10] if k == "sale_date" else v if k == "store" else _num(v)) for k, v in zip(KEYS, r)}
            for r in raw]


def run_local(rows: list[dict]) -> dict[str, list[dict]]:
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE daily_units (sale_date DATE, store VARCHAR, units INTEGER)")
    con.executemany("INSERT INTO daily_units VALUES (?, ?, ?)",
                    [(r["sale_date"], r["store"], int(r["units"])) for r in rows])
    ref = (HERE / "local_reference.sql").read_text()
    return {"by_store": normalise(con.sql(ref.replace("{grain}", "store")).fetchall()),
            "all_stores": normalise(con.sql(ref.replace("{grain}", "'all'")).fetchall())}


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
        print("::error title=Databricks day 4::No SQL warehouse in the workspace")
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
        print(f"::{'error' if fail else 'warning'} title=Databricks day 4 {name} {res['status']['state']}::{msg}")
        if fail:
            raise SystemExit(1)
        return None
    return res


def run_databricks(rows: list[dict]) -> dict[str, list[dict]]:
    wh = _warehouse()
    statement(wh, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}", "create schema")
    try:
        statement(wh, table_sql(rows), "create table")
        statement(wh, metric_view_sql(), "create metric view")
        return {name: normalise(statement(wh, sql, name)["result"].get("data_array", []))
                for name, sql in queries().items()}
    finally:  # leave nothing behind
        statement(wh, f"DROP VIEW IF EXISTS {SCHEMA}.store_sales_metrics", "drop metric view", fail=False)
        statement(wh, f"DROP TABLE IF EXISTS {SCHEMA}.daily_units", "drop table", fail=False)
        statement(wh, f"DROP SCHEMA IF EXISTS {SCHEMA}", "drop schema", fail=False)


def spikes(rows: list[dict]) -> list[dict]:
    """Days with a full 7-day history that sold SPIKE x their prior-7-day average per trading day."""
    first = min(r["sale_date"] for r in rows)
    from datetime import date, timedelta
    full_from = (date.fromisoformat(first) + timedelta(days=7)).isoformat()
    out = []
    for r in rows:
        if r["sale_date"] >= full_from and r["open_days_prior_7d"]:
            ratio = r["units_sold"] / (r["units_prior_7d"] / r["open_days_prior_7d"])
            if ratio > SPIKE:
                out.append({**r, "vs_prior_avg": round(ratio, 2)})
    return out


def summarise(res: dict[str, list[dict]]) -> dict:
    allr = res["all_stores"]
    by_store_end = {}
    for r in res["by_store"]:
        by_store_end[r["store"]] = r["units_mtd"]
    return {
        "days": len(allr),
        "month_to_date_units": allr[-1]["units_mtd"],
        "month_to_date_by_store": by_store_end,
        "last_day_vs_prior_7d_avg": round(allr[-1]["units_sold"] / (allr[-1]["units_prior_7d"] / allr[-1]["open_days_prior_7d"]), 2),
        "spike_days": [{k: s[k] for k in ("store", "sale_date", "units_sold", "vs_prior_avg")}
                       for s in spikes(res["by_store"])],
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    rows = load_rows()
    local = run_local(rows)
    if mode == "--databricks":
        cloud = run_databricks(rows)
        for name in cloud:
            print(name, cloud[name][:3], "...")
        if cloud != local:
            diff = [(n, c, l) for n in local for c, l in zip(cloud.get(n, []), local[n]) if c != l][:6] \
                or [(n, len(cloud.get(n, [])), len(local[n])) for n in local]
            print(f"::error title=Databricks result differs from local::{json.dumps(diff)[:900]}")
            raise SystemExit(1)
        print("::notice title=Databricks day 4::Metric view window measures (trailing 7 day, cumulative) ran on a "
              f"serverless SQL warehouse and matched local: {json.dumps(summarise(cloud))[:700]}")
    else:
        summary = summarise(local)
        (HERE / "result.json").write_text(json.dumps({"engine": "duckdb window functions (local) / Databricks SQL "
                                                      "Unity Catalog metric view window measures (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
