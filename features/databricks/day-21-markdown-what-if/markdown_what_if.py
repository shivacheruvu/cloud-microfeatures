"""Day 21 (Databricks): markdown what-if KPIs from one Unity Catalog metric view with a query-time PARAMETER.

  python markdown_what_if.py --local        # DuckDB: the same measures written by hand; writes result.json
  python markdown_what_if.py --databricks   # serverless SQL warehouse via the Statement Execution API: create a
                                            # scratch table + parameterised metric view, query it at four markdown
                                            # levels (and once with the default), compare with local, drop all

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
SCHEMA = "microfeatures_day21"
MARKDOWNS = ["0.0", "0.1", "0.2", "0.3"]  # SQL literals: shelf price, 10%, 20%, 30% off
KEYS = ["department", "markdown", "units", "net_sales", "margin", "lines_below_cost"]


def load_rows() -> list[dict]:
    with open(HERE / "data" / "week_sales.csv", newline="") as f:
        return list(csv.DictReader(f))


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def table_sql(rows: list[dict], schema: str = SCHEMA) -> str:
    values = ",\n".join(f"  ({_q(r['product'])}, {_q(r['department'])}, {r['shelf_price']}, {r['unit_cost']}, "
                        f"{int(r['units'])})" for r in rows)
    return (f"CREATE OR REPLACE TABLE {schema}.week_sales AS\n"
            f"SELECT product, department, CAST(shelf_price AS DECIMAL(6,2)) AS shelf_price,\n"
            f"       CAST(unit_cost AS DECIMAL(6,2)) AS unit_cost, CAST(units AS INT) AS units FROM VALUES\n"
            f"{values}\nAS t(product, department, shelf_price, unit_cost, units)")


def metric_view_sql(schema: str = SCHEMA) -> str:
    return (HERE / "metric_view.sql").read_text().replace("{schema}", schema)


def queries(schema: str = SCHEMA) -> dict[str, str]:
    """{'by_department': sql with {markdown}, 'default': sql} from queries.sql ('-- name:' comment per statement)."""
    out = {}
    for chunk in (HERE / "queries.sql").read_text().split(";"):
        chunk = chunk.strip()
        if chunk:
            name = chunk.split(":", 1)[0].removeprefix("--").strip()
            out[name] = chunk.replace("{schema}", schema)
    return out


def normalise(raw: list[list]) -> list[dict]:
    out = []
    for r in raw:
        d = dict(zip(KEYS, r))
        out.append({"department": d["department"], "markdown": round(float(d["markdown"]), 2),
                    "units": int(float(d["units"])), "net_sales": round(float(d["net_sales"]), 2),
                    "margin": round(float(d["margin"]), 2), "lines_below_cost": int(float(d["lines_below_cost"]))})
    return out


def total(rows: list[dict]) -> dict:
    return {"department": "all", "markdown": rows[0]["markdown"], "units": sum(r["units"] for r in rows),
            "net_sales": round(sum(r["net_sales"] for r in rows), 2),
            "margin": round(sum(r["margin"] for r in rows), 2),
            "lines_below_cost": sum(r["lines_below_cost"] for r in rows)}


def run_local(rows: list[dict]) -> dict[str, list[dict]]:
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE week_sales (product VARCHAR, department VARCHAR, shelf_price DECIMAL(6,2), "
                "unit_cost DECIMAL(6,2), units INTEGER)")
    con.executemany("INSERT INTO week_sales VALUES (?, ?, ?, ?, ?)",
                    [(r["product"], r["department"], r["shelf_price"], r["unit_cost"], int(r["units"]))
                     for r in rows])
    ref = (HERE / "local_reference.sql").read_text()
    out = {}
    for m in MARKDOWNS:
        # DOUBLE like the metric view parameter, so rounding to cents happens on the same values
        out[f"markdown_{m}"] = normalise(con.sql(ref.replace("{markdown}", f"CAST({m} AS DOUBLE)")).fetchall())
    out["default"] = [total(out["markdown_0.0"])]
    return out


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
        print("::error title=Databricks day 21::No SQL warehouse in the workspace")
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
        print(f"::{'error' if fail else 'warning'} title=Databricks day 21 {name} {res['status']['state']}::{msg}")
        if fail:
            raise SystemExit(1)
        return None
    return res


def run_databricks(rows: list[dict]) -> dict[str, list[dict]]:
    wh = _warehouse()
    q = queries()
    statement(wh, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}", "create schema")
    try:
        statement(wh, table_sql(rows), "create table")
        statement(wh, metric_view_sql(), "create metric view")
        out = {}
        for m in MARKDOWNS:
            res = statement(wh, q["by_department"].replace("{markdown}", m), f"markdown {m}")
            out[f"markdown_{m}"] = normalise(res["result"].get("data_array", []))
        out["default"] = normalise(statement(wh, q["default"], "default")["result"].get("data_array", []))
        return out
    finally:  # leave nothing behind
        statement(wh, f"DROP VIEW IF EXISTS {SCHEMA}.markdown_metrics", "drop metric view", fail=False)
        statement(wh, f"DROP TABLE IF EXISTS {SCHEMA}.week_sales", "drop table", fail=False)
        statement(wh, f"DROP SCHEMA IF EXISTS {SCHEMA}", "drop schema", fail=False)


def deepest_safe_markdown(res: dict[str, list[dict]]) -> dict[str, str]:
    """Per department, the deepest tested markdown that leaves no line below cost."""
    out = {}
    for m in MARKDOWNS:
        for r in res[f"markdown_{m}"]:
            if r["lines_below_cost"] == 0:
                out[r["department"]] = f"{round(float(m) * 100)}%"
            else:
                out.setdefault(r["department"], "none")
    return out


def summarise(res: dict[str, list[dict]]) -> dict:
    levels = []
    for m in MARKDOWNS:
        t = total(res[f"markdown_{m}"])
        levels.append({"markdown": f"{round(float(m) * 100)}%", "net_sales": t["net_sales"], "margin": t["margin"],
                       "lines_below_cost": t["lines_below_cost"]})
    base = res["default"][0]
    return {
        "lines": None,  # filled by caller
        "units": base["units"],
        "shelf_price_sales": base["net_sales"],
        "shelf_price_margin": base["margin"],
        "levels": levels,
        "deepest_markdown_with_no_line_below_cost": deepest_safe_markdown(res),
    }


def summary_for(rows: list[dict], res: dict[str, list[dict]]) -> dict:
    s = summarise(res)
    s["lines"] = len(rows)
    return s


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    rows = load_rows()
    local = run_local(rows)
    if mode == "--databricks":
        cloud = run_databricks(rows)
        for name in cloud:
            print(name, cloud[name][:2], "...")
        if cloud != local:
            diff = [(n, c, l) for n in local for c, l in zip(cloud.get(n, []), local[n]) if c != l][:6] \
                or [(n, len(cloud.get(n, [])), len(local[n])) for n in local]
            print(f"::error title=Databricks result differs from local::{json.dumps(diff)[:900]}")
            raise SystemExit(1)
        print("::notice title=Databricks day 21::One parameterised metric view answered 4 markdown levels "
              "(markdown => 0 / 0.1 / 0.2 / 0.3) plus the default on a serverless SQL warehouse and matched local: "
              f"{json.dumps(summary_for(rows, cloud))[:700]}")
    else:
        summary = summary_for(rows, local)
        (HERE / "result.json").write_text(json.dumps({"engine": "duckdb hand-written measures (local) / Databricks "
                                                      "SQL metric view with a query-time parameter (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
