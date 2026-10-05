"""Day 1 (Databricks): scan rush hours with the new time_bucket SQL function.

  python rush_hours.py --local        # DuckDB (same SQL), no cloud needed; writes result.json
  python rush_hours.py --databricks   # serverless SQL warehouse via the Statement Execution API

Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed
about the workspace except the query results.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLOT_MINUTES, ORIGIN_MINUTE, HEALTHY = 30, 15, 70


def load_rows() -> list[dict]:
    with open(HERE / "data" / "scans.csv", newline="") as f:
        return list(csv.DictReader(f))


def build_query(rows: list[dict]) -> str:
    def lit(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"
    values = ",\n".join(
        f"    ({lit(r['scanned_at'])}, {lit(r['barcode'])}, {lit(r['category'])}, {int(r['health_score'])})" for r in rows
    )
    return (HERE / "rush_hours.sql").read_text().replace("{values}", values)


def normalise(raw: list[list]) -> list[dict]:
    out = []
    for slot, scans, avg, pct in raw:
        slot = str(slot).replace("T", " ")[:16]
        out.append({"slot_start": slot, "scans": int(scans), "avg_health_score": round(float(avg), 1),
                    "pct_healthy": round(float(pct), 1)})
    return out


def run_local(sql: str) -> list[dict]:
    import duckdb
    return normalise(duckdb.sql(sql).fetchall())


def _api(method: str, path: str, body: dict | None = None) -> dict:
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    req = urllib.request.Request(host + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def run_databricks(sql: str) -> list[dict]:
    warehouses = _api("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
    if not warehouses:
        raise SystemExit("No SQL warehouse in the workspace")
    wh = next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh["id"], "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        raise SystemExit(f"Statement {res['status']['state']}: {res['status'].get('error', {}).get('message', '')}")
    return normalise(res["result"]["data_array"])


def summarise(slots: list[dict]) -> dict:
    busiest = max(slots, key=lambda s: s["scans"])
    healthiest = max(slots, key=lambda s: s["avg_health_score"])
    return {
        "scans": sum(s["scans"] for s in slots),
        "slots": len(slots),
        "busiest_slot": busiest["slot_start"][11:],
        "busiest_slot_scans": busiest["scans"],
        "healthiest_slot": healthiest["slot_start"][11:],
        "healthiest_slot_avg_score": healthiest["avg_health_score"],
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    sql = build_query(load_rows())
    local = run_local(sql)
    if mode == "--databricks":
        cloud = run_databricks(sql)
        for s in cloud:
            print(f"{s['slot_start'][11:]}  scans={s['scans']:>2}  avg_score={s['avg_health_score']:>5}  healthy={s['pct_healthy']}%")
        if cloud != local:
            raise SystemExit("Databricks result differs from the local DuckDB result")
        print(f"::notice title=Databricks day 1::time_bucket ran on a serverless SQL warehouse: {json.dumps(summarise(cloud))}")
    else:
        summary = summarise(local)
        (HERE / "result.json").write_text(json.dumps({"engine": "duckdb (local) / Databricks SQL (cloud)", **summary,
                                                      "by_slot": local}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
