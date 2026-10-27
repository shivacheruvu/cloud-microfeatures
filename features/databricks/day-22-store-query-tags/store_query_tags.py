"""Day 22 (Databricks): tag each store's daily-close report with query tags, then attribute the work per store.

  python store_query_tags.py --local        # DuckDB runs the same report per store; simulated tag rollup;
                                            # writes result.json
  python store_query_tags.py --databricks   # serverless SQL warehouse via the Statement Execution API: each report
                                            # is sent with `query_tags` [store, report, feature], results are
                                            # compared with local, then the tags are read back from
                                            # system.query.history and rolled up per store

Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed about
the workspace except query results and tag values defined in this file.
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
SCHEMA = "microfeatures_day22"
STORES = ["east", "north", "south"]
KEYS = ["department", "units", "net_sales", "lines"]
REPORT = "daily_close"
FEATURE_TAG = "day22"
# Documented limits for query tags: at most 20 per query, keys and values up to 128 characters, keys may not contain
# , : - / = . and keys starting with @@ are reserved.
BAD_KEY = re.compile(r"[,:\-/=.]")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def load_rows() -> list[dict]:
    with open(HERE / "data" / "store_sales.csv", newline="") as f:
        return list(csv.DictReader(f))


def tags_for(store: str) -> list[dict]:
    """Statement-level tags, in the Statement Execution API's `query_tags` shape."""
    return [{"key": "store", "value": store}, {"key": "report", "value": REPORT},
            {"key": "feature", "value": FEATURE_TAG}]


def check_tags(tags: list[dict]) -> list[str]:
    problems = []
    if len(tags) > 20:
        problems.append("more than 20 tags")
    for t in tags:
        k, v = t["key"], t.get("value")
        if not k or len(k) > 128 or BAD_KEY.search(k) or k.startswith("@@"):
            problems.append(f"bad key {k!r}")
        if v is not None and len(v) > 128:
            problems.append(f"value too long for {k!r}")
    return problems


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def table_sql(rows: list[dict], schema: str = SCHEMA) -> str:
    values = ",\n".join(f"  ({_q(r['store'])}, {_q(r['department'])}, {_q(r['product'])}, {int(r['units'])}, "
                        f"{r['net_sales']})" for r in rows)
    return (f"CREATE OR REPLACE TABLE {schema}.store_sales AS\n"
            f"SELECT store, department, product, CAST(units AS INT) AS units,\n"
            f"       CAST(net_sales AS DECIMAL(10,2)) AS net_sales FROM VALUES\n"
            f"{values}\nAS t(store, department, product, units, net_sales)")


def report_sql(store: str, schema: str = SCHEMA) -> str:
    if store not in STORES:
        raise ValueError(store)
    body = (HERE / "daily_close.sql").read_text().split("\n", 1)[1]  # drop the '-- name:' line
    return body.replace("{schema}", schema).replace("{store}", store).strip()


def history_sql(statement_ids: list[str]) -> str:
    if not statement_ids or not all(UUID.match(s) for s in statement_ids):
        raise ValueError("statement ids must be UUIDs")
    body = (HERE / "history.sql").read_text().split("\n", 1)[1]
    return body.replace("{statement_ids}", ", ".join(_q(s) for s in statement_ids)).strip()


def normalise(raw: list[list]) -> list[dict]:
    out = []
    for r in raw:
        d = dict(zip(KEYS, r))
        out.append({"department": d["department"], "units": int(float(d["units"])),
                    "net_sales": round(float(d["net_sales"]), 2), "lines": int(float(d["lines"]))})
    return out


def run_local(rows: list[dict]) -> dict[str, list[dict]]:
    import duckdb
    con = duckdb.connect()
    con.execute(f"CREATE SCHEMA {SCHEMA}")
    con.execute(f"CREATE TABLE {SCHEMA}.store_sales (store VARCHAR, department VARCHAR, product VARCHAR, "
                "units INTEGER, net_sales DECIMAL(10,2))")
    con.executemany(f"INSERT INTO {SCHEMA}.store_sales VALUES (?, ?, ?, ?, ?)",
                    [(r["store"], r["department"], r["product"], int(r["units"]), r["net_sales"]) for r in rows])
    return {s: normalise(con.sql(report_sql(s)).fetchall()) for s in STORES}


def rollup(history: list[dict]) -> list[dict]:
    """What history.sql computes, in Python: statements and produced rows per (store, report) for day22 tags."""
    agg: dict[tuple, dict] = {}
    for h in history:
        tags = h.get("query_tags") or {}
        if tags.get("feature") != FEATURE_TAG:
            continue
        key = (tags.get("store"), tags.get("report"))
        a = agg.setdefault(key, {"store": key[0], "report": key[1], "statements": 0, "produced_rows": 0})
        a["statements"] += 1
        a["produced_rows"] += int(h["produced_rows"])
    return [agg[k] for k in sorted(agg, key=lambda k: (k[0] or "", k[1] or ""))]


def simulated_history(results: dict[str, list[dict]]) -> list[dict]:
    """The history rows the warehouse should record for the tagged statements (one per store)."""
    return [{"query_tags": {t["key"]: t["value"] for t in tags_for(s)}, "produced_rows": len(results[s])}
            for s in STORES]


# ---------------------------------------------------------------- Databricks
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
        print("::error title=Databricks day 22::No SQL warehouse in the workspace")
        raise SystemExit(1)
    return next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])["id"]


def statement(wh: str, sql: str, name: str, fail: bool = True, tags: list[dict] | None = None) -> dict | None:
    body = {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"}
    if tags:
        body["query_tags"] = tags
    res = _api("POST", "/api/2.0/sql/statements", body)
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")[:500].replace("\n", " ")
        print(f"::{'error' if fail else 'warning'} title=Databricks day 22 {name} {res['status']['state']}::{msg}")
        if fail:
            raise SystemExit(1)
        return None
    return res


def read_history(wh: str, ids: list[str], attempts: int = 12) -> list[dict] | None:
    """Poll system.query.history until all tagged statements appear (it is not real-time)."""
    for i in range(attempts):
        res = statement(wh, history_sql(ids), "query history", fail=False)
        if res is None:
            return None  # e.g. no SELECT on system.query (needs an account admin grant)
        rows = [dict(zip(["store", "report", "statements", "produced_rows"], r))
                for r in res["result"].get("data_array", []) or []]
        rows = [{**r, "statements": int(r["statements"]), "produced_rows": int(r["produced_rows"])} for r in rows]
        if sum(r["statements"] for r in rows) == len(ids):
            return rows
        time.sleep(30)
    return []


def run_databricks(rows: list[dict]) -> tuple[dict[str, list[dict]], list[dict] | None]:
    wh = _warehouse()
    statement(wh, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}", "create schema")
    try:
        statement(wh, table_sql(rows), "create table")
        out, ids = {}, []
        for s in STORES:
            res = statement(wh, report_sql(s), f"daily close {s}", tags=tags_for(s))
            ids.append(res["statement_id"])
            out[s] = normalise(res["result"].get("data_array", []))
        return out, read_history(wh, ids)
    finally:  # leave nothing behind
        statement(wh, f"DROP TABLE IF EXISTS {SCHEMA}.store_sales", "drop table", fail=False)
        statement(wh, f"DROP SCHEMA IF EXISTS {SCHEMA}", "drop schema", fail=False)


def summarise(rows: list[dict], res: dict[str, list[dict]], roll: list[dict]) -> dict:
    stores = []
    for s in STORES:
        stores.append({"store": s, "units": sum(r["units"] for r in res[s]),
                       "net_sales": round(sum(r["net_sales"] for r in res[s]), 2),
                       "top_department": max(res[s], key=lambda r: r["net_sales"])["department"]})
    return {"lines": len(rows), "stores": stores, "tags_per_statement": [t["key"] for t in tags_for(STORES[0])],
            "tag_rollup": roll}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    rows = load_rows()
    local = run_local(rows)
    expected_roll = rollup(simulated_history(local))
    if mode == "--databricks":
        cloud, roll = run_databricks(rows)
        if cloud != local:
            diff = [(s, cloud.get(s), local[s]) for s in STORES if cloud.get(s) != local[s]][:3]
            print(f"::error title=Databricks result differs from local::{json.dumps(diff)[:900]}")
            raise SystemExit(1)
        if roll is None:
            print("::warning title=Databricks day 22::Reports matched local, but system.query.history could not be "
                  "read (an account admin must grant SELECT on system.query), so the tags were not verified")
        elif roll == []:
            print("::warning title=Databricks day 22::Reports matched local; the tagged statements had not reached "
                  "system.query.history after ~6 minutes, so the tag rollup was not verified this run")
        elif roll != expected_roll:
            print(f"::error title=Databricks day 22 tag rollup differs::{json.dumps(roll)[:600]} "
                  f"vs expected {json.dumps(expected_roll)[:600]}")
            raise SystemExit(1)
        else:
            print("::notice title=Databricks day 22::3 store reports ran with query_tags on a serverless SQL warehouse, "
                  "matched local, and system.query.history attributed them per store: "
                  f"{json.dumps(summarise(rows, cloud, roll))[:700]}")
    else:
        summary = summarise(rows, local, expected_roll)
        (HERE / "result.json").write_text(json.dumps({"engine": "duckdb (local) / Databricks SQL warehouse with "
                                                      "statement-level query tags (cloud)", **summary}, indent=2)
                                          + "\n")
        print(json.dumps(summary, indent=2))
