"""Day 36 (Databricks): a daily markdown-planning job whose dates all come from job parameters resolved with date
arithmetic in dynamic value references (Lakeflow Jobs, Beta, 2026-11-09).

  python markdown_window.py    # local: resolve the job's parameters for a run that starts 2026-11-09T11:00:07.123Z,
                               # run markdown_plan.sql in DuckDB with them, check the edge cases; writes result.json

The cloud run (run_cloud.py) creates the job with the same references as task parameters, runs it once on serverless
and checks every value the task received equals date_refs.resolve() for that run's own start time.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import date_refs  # noqa: E402

LOCAL_START = datetime(2026, 11, 9, 11, 0, 7, 123000, tzinfo=timezone.utc)
DATE_PARAMS = ("run_date", "yesterday", "yesterday_last_week", "week_start", "sell_by_cutoff")
# Edge cases the resolver must get right; months/years clamping is this feature's documented assumption.
EDGES = [
    ("2026-09-22T04:00:00Z", "{{(job.start_time - 6.hours).iso_date}}", "2026-09-21"),   # documented example
    ("2026-09-22T04:00:00Z", "{{(job.start_time + 1.week).iso_date}}", "2026-09-29"),    # documented example
    ("2026-09-22T04:00:00Z", "{{(job.start_time - 1.month).month}}", "8"),               # documented example
    ("2026-09-22T04:00:00Z", "{{(job.start_time - 1.day).timestamp_ms}}", "1789963200000"),  # documented example
    ("2026-11-09T02:30:00Z", "{{(job.start_time - 1.day).iso_date}}", "2026-11-08"),     # UTC, not Chicago
    ("2026-03-31T09:00:00Z", "{{(job.start_time - 1.month).iso_date}}", "2026-02-28"),   # clamp (assumed)
    ("2028-02-29T09:00:00Z", "{{(job.start_time - 1.year).iso_date}}", "2027-02-28"),    # leap day (assumed)
    ("2026-12-31T23:30:00Z", "{{(job.start_time + 45.minutes).year}}", "2027"),
    ("2026-11-09T11:00:00Z", "{{(job.start_time + 2.days).is_weekday}}", "true"),
    ("2026-11-09T11:00:00Z", "{{(job.start_time + 1.months).iso_date}}", "2026-12-09"),  # plural unit
]


def _iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def edge_results() -> list[dict]:
    return [{"start": s, "ref": r, "want": w, "got": date_refs.resolve(r, _iso(s))} for s, r, w in EDGES]


def bind(sql: str, params: dict) -> str:
    def lit(m: re.Match) -> str:
        v = params[m[1]]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError(f"{m[1]} is not an ISO date: {v!r}")
        return f"'{v}'"
    return re.sub(r":([a-z_]+)", lit, sql)


def plan(params: dict) -> tuple[list[dict], dict]:
    con = duckdb.connect()
    con.execute(f"CREATE TABLE sales AS SELECT * FROM read_csv('{HERE / 'data' / 'sales.csv'}', "
                "columns={'store':'VARCHAR','sku':'VARCHAR','sale_date':'DATE','units':'INTEGER'}, header=true)")
    con.execute(f"CREATE TABLE lots AS SELECT * FROM read_csv('{HERE / 'data' / 'lots.csv'}', "
                "columns={'store':'VARCHAR','sku':'VARCHAR','lot':'VARCHAR','sell_by':'DATE','on_hand':'INTEGER'}, "
                "header=true)")
    cur = con.execute(bind((HERE / "markdown_plan.sql").read_text(), params))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["per_day"] = float(r["per_day"])
    yday = dict(con.execute(bind("SELECT CAST(sale_date AS VARCHAR), SUM(units) FROM sales WHERE sale_date IN "
                                 "(CAST(:yesterday AS DATE), CAST(:yesterday_last_week AS DATE)) GROUP BY 1",
                                 params)).fetchall())
    return rows, {"yesterday": int(yday.get(params["yesterday"], 0)),
                  "same_weekday_last_week": int(yday.get(params["yesterday_last_week"], 0))}


def summarise(params: dict, rows: list[dict], units: dict, edges: list[dict]) -> dict:
    actions = {a: sum(r["action"] == a for r in rows) for a in ("pull", "50% off", "25% off", "keep")}
    return {
        "run_start_utc": LOCAL_START.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "parameters": {k: date_refs.PARAMS[k] for k in date_refs.PARAMS},
        "resolved": params,
        "lots_considered": len(rows),
        "actions": actions,
        "units_to_mark_down": sum(r["on_hand"] for r in rows if r["action"].endswith("off")),
        "units_to_pull": sum(r["on_hand"] for r in rows if r["action"] == "pull"),
        "store_units": units,
        "edge_cases_passed": f"{sum(e['got'] == e['want'] for e in edges)}/{len(edges)}",
        "plan": rows,
    }


def main() -> int:
    params = date_refs.resolve_all(LOCAL_START)
    rows, units = plan(params)
    edges = edge_results()
    bad = [e for e in edges if e["got"] != e["want"]]
    s = summarise(params, rows, units, edges)
    (HERE / "result.json").write_text(json.dumps({"engine": "date_refs.py resolver + DuckDB (local) / Lakeflow Jobs "
                                                  "task parameters on serverless (cloud)", **s}, indent=2) + "\n")
    print(json.dumps({k: v for k, v in s.items() if k != "plan"}, indent=2))
    for e in bad:
        print(f"edge case failed: {e}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
