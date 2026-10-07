"""Day 2 (Databricks): items scanned per checkout lane from reboot-prone cumulative counters, with counter_diff.

  python lane_counters.py --local        # DuckDB (counter_diff translated to LAG + reset check); writes result.json
  python lane_counters.py --databricks   # serverless SQL warehouse via the Statement Execution API

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
COUNTER_DIFF = re.compile(r"counter_diff\((\w+)\) OVER \(([^)]*)\)")


def load_rows() -> list[dict]:
    with open(HERE / "data" / "lane_counters.csv", newline="") as f:
        return list(csv.DictReader(f))


def build_query(rows: list[dict]) -> str:
    def lit(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"
    values = ",\n".join(f"    ({lit(r['lane'])}, {lit(r['read_at'])}, {int(r['scan_counter'])})" for r in rows)
    return (HERE / "lane_counters.sql").read_text().replace("{values}", values)


def to_duckdb(sql: str) -> str:
    """DuckDB has no counter_diff: same semantics with LAG (NULL on the first row and when the counter drops)."""
    def repl(m: re.Match) -> str:
        v, w = m[1], m[2]
        prev = f"LAG({v}) OVER ({w})"
        return f"CASE WHEN {prev} IS NULL OR {prev} > {v} THEN NULL ELSE {v} - {prev} END"
    return COUNTER_DIFF.sub(repl, sql)


def normalise(raw: list[list]) -> list[dict]:
    keys = ["lane", "readings", "items_scanned", "resets", "peak_items_15min", "naive_max_minus_min"]
    return [{k: (v if k == "lane" else int(v)) for k, v in zip(keys, r)} for r in raw]


def run_local(sql: str) -> list[dict]:
    import duckdb
    return normalise(duckdb.sql(to_duckdb(sql)).fetchall())


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


def run_databricks(sql: str) -> list[dict]:
    warehouses = _api("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
    if not warehouses:
        print("::error title=Databricks day 2::No SQL warehouse in the workspace")
        raise SystemExit(1)
    wh = next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh["id"], "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")[:500].replace("\n", " ")
        print(f"::error title=Databricks day 2 statement {res['status']['state']}::{msg}")
        raise SystemExit(1)
    return normalise(res["result"]["data_array"])


def summarise(lanes: list[dict]) -> dict:
    busiest = max(lanes, key=lambda r: r["items_scanned"])
    return {
        "lanes": len(lanes),
        "items_scanned": sum(r["items_scanned"] for r in lanes),
        "resets_handled": sum(r["resets"] for r in lanes),
        "busiest_lane": busiest["lane"],
        "busiest_lane_items": busiest["items_scanned"],
        "naive_max_minus_min_total": sum(r["naive_max_minus_min"] for r in lanes),
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    sql = build_query(load_rows())
    local = run_local(sql)
    if mode == "--databricks":
        cloud = run_databricks(sql)
        for r in cloud:
            print(r)
        if cloud != local:
            diff = [(c, l) for c, l in zip(cloud, local) if c != l] or [("rows", len(cloud), len(local))]
            print(f"::error title=Databricks result differs from local::{json.dumps(diff)[:900]}")
            raise SystemExit(1)
        print(f"::notice title=Databricks day 2::counter_diff ran on a serverless SQL warehouse: {json.dumps(summarise(cloud))}")
    else:
        summary = summarise(local)
        (HERE / "result.json").write_text(json.dumps({"engine": "duckdb (local) / Databricks SQL (cloud)", **summary,
                                                      "by_lane": local}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
