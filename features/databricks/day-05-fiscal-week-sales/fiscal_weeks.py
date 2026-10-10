"""Day 5 (Databricks): fiscal-week grocery sales from a Unity Catalog metric view whose window measures run on a
numeric index column (a dense fiscal-week number) instead of a date.

  python fiscal_weeks.py --local        # DuckDB: the same measures as integer RANGE window functions; writes result.json
  python fiscal_weeks.py --databricks   # serverless SQL warehouse via the Statement Execution API: create a scratch
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
SCHEMA = "microfeatures_day05"
KEYS = ["store", "week_index", "units_sold", "units_prev_week", "units_prior_4wk", "units_to_date"]
SPIKE = 1.25  # a week is a spike when it sells 25% more than the average of the 4 weeks before it


def load_rows() -> list[dict]:
    with open(HERE / "data" / "weekly_units.csv", newline="") as f:
        return list(csv.DictReader(f))


def week_labels(rows: list[dict]) -> dict[int, str]:
    return {int(r["week_index"]): f"FY{r['fiscal_year']} W{int(r['fiscal_week']):02d}" for r in rows}


def check_dense(rows: list[dict]) -> None:
    """Numeric-index windows need an index that rises by exactly 1 per week; gaps give wrong numbers silently."""
    idx = sorted({int(r["week_index"]) for r in rows})
    if idx != list(range(idx[0], idx[0] + len(idx))):
        raise ValueError(f"week_index is not dense: {idx}")
    labels = week_labels(rows)
    if len(set(labels.values())) != len(labels):
        raise ValueError("two week_index values map to the same fiscal week")


def table_sql(rows: list[dict], schema: str = SCHEMA) -> str:
    values = ",\n".join(f"  ({int(r['week_index'])}, {int(r['fiscal_year'])}, {int(r['fiscal_week'])}, "
                        f"'{r['store']}', {int(r['units'])})" for r in rows)
    return (f"CREATE OR REPLACE TABLE {schema}.weekly_units AS\n"
            f"SELECT CAST(week_index AS INT) AS week_index, CAST(fiscal_year AS INT) AS fiscal_year,\n"
            f"       CAST(fiscal_week AS INT) AS fiscal_week, store, CAST(units AS INT) AS units FROM VALUES\n"
            f"{values}\nAS t(week_index, fiscal_year, fiscal_week, store, units)")


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
    return [{k: (v if k == "store" else _num(v)) for k, v in zip(KEYS, r)} for r in raw]


def run_local(rows: list[dict]) -> dict[str, list[dict]]:
    import duckdb
    check_dense(rows)
    con = duckdb.connect()
    con.execute("CREATE TABLE weekly_units (week_index INTEGER, fiscal_year INTEGER, fiscal_week INTEGER, "
                "store VARCHAR, units INTEGER)")
    con.executemany("INSERT INTO weekly_units VALUES (?, ?, ?, ?, ?)",
                    [(int(r["week_index"]), int(r["fiscal_year"]), int(r["fiscal_week"]), r["store"],
                      int(r["units"])) for r in rows])
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
        print("::error title=Databricks day 5::No SQL warehouse in the workspace")
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
        print(f"::{'error' if fail else 'warning'} title=Databricks day 5 {name} {res['status']['state']}::{msg}")
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
        statement(wh, f"DROP VIEW IF EXISTS {SCHEMA}.weekly_sales_metrics", "drop metric view", fail=False)
        statement(wh, f"DROP TABLE IF EXISTS {SCHEMA}.weekly_units", "drop table", fail=False)
        statement(wh, f"DROP SCHEMA IF EXISTS {SCHEMA}", "drop schema", fail=False)


def spikes(rows: list[dict], labels: dict[int, str]) -> list[dict]:
    """Weeks with a full 4-week history that sold SPIKE x the average of the 4 weeks before."""
    first = min(r["week_index"] for r in rows)
    out = []
    for r in rows:
        if r["week_index"] >= first + 4:
            ratio = r["units_sold"] / (r["units_prior_4wk"] / 4)
            if ratio > SPIKE:
                out.append({"store": r["store"], "week": labels[r["week_index"]], "units_sold": r["units_sold"],
                            "vs_prior_4wk_avg": round(ratio, 2)})
    return out


def summarise(res: dict[str, list[dict]], labels: dict[int, str]) -> dict:
    allr = res["all_stores"]
    by_store_end = {r["store"]: r["units_to_date"] for r in res["by_store"]}
    boundary = next(r for r in allr if labels[r["week_index"]].endswith("W01"))
    return {
        "weeks": len(allr),
        "first_week": labels[allr[0]["week_index"]],
        "last_week": labels[allr[-1]["week_index"]],
        "units_to_date": allr[-1]["units_to_date"],
        "units_to_date_by_store": by_store_end,
        "year_boundary": {"week": labels[boundary["week_index"]], "units_sold": boundary["units_sold"],
                          "prev_week": labels[boundary["week_index"] - 1],
                          "units_prev_week": boundary["units_prev_week"]},
        "spike_weeks": spikes(res["by_store"], labels),
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    rows = load_rows()
    labels = week_labels(rows)
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
        print("::notice title=Databricks day 5::Metric view window measures on a numeric fiscal-week index "
              "(offset -1, trailing 4, cumulative) ran on a serverless SQL warehouse and matched local: "
              f"{json.dumps(summarise(cloud, labels))[:700]}")
    else:
        summary = summarise(local, labels)
        (HERE / "result.json").write_text(json.dumps({"engine": "duckdb integer RANGE windows (local) / Databricks "
                                                      "SQL metric view window measures on a numeric index (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
