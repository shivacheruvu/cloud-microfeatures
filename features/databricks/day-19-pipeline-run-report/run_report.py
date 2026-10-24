"""Day 19 (Databricks): a pipeline run report straight from the Lakeflow pipelines system tables
(`system.lakeflow.pipelines` and `system.lakeflow.pipeline_update_timeline`, GA 2026-10-21). For every pipeline this
project creates (name LIKE 'microfeatures-%'), report.sql counts the updates of the last 7 days, how they ended, how
long they ran and how many short-lived pipeline IDs the daily runs created and deleted.

  python run_report.py --local        # DuckDB runs report.sql over a synthetic copy of the two tables; writes result.json
  python run_report.py --databricks   # Free Edition: run report.sql on the serverless SQL warehouse (last 7 days)

The cloud check (errors):
  * the system tables can't be read (no access, or system.lakeflow isn't available)
  * no updates of yesterday's day-18 pipeline (microfeatures-day18-store-params) in the last 7 days
  * a report row whose counts don't add up (completed + failed + canceled + running != updates, deleted > IDs)
  * the same report over the old schema name (system.workflow, documented as identical) gives different numbers
    (only a warning if system.workflow isn't there)
Real system-table rows can't equal a synthetic local copy, so the local run checks the SQL itself: DuckDB's answer
over the fixture must equal a plain-Python model of the same rules. Nothing about the workspace (IDs, host, users) is
printed; only pipeline names that start with 'microfeatures-' and counts.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME_LIKE = "microfeatures-%"
EXPECTED = "microfeatures-day18-store-params"          # day 18's pipeline: two updates per daily run, then deleted
COLUMNS = ["pipeline", "pipeline_ids", "deleted_ids", "updates", "completed", "failed", "canceled", "running",
           "full_refreshes", "avg_seconds", "max_seconds", "last_result"]

# ---------- synthetic copy of the two system tables (local run and tests)
NOW = datetime(2026, 10, 23, 15, 0)                     # fixture "now" (UTC); the report looks back 7 days
SINCE = NOW - timedelta(days=7)
WS = "ws-1"


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _fixture() -> tuple[list[tuple], list[tuple]]:
    pipelines, timeline = [], []    # pipelines: (workspace_id, pipeline_id, name, change_time, delete_time)

    def short_lived(pid, name, created, deleted, null_name_on_delete=False):
        pipelines.append((WS, pid, name, _t(created), None))
        pipelines.append((WS, pid, None if null_name_on_delete else name, _t(deleted), _t(deleted)))

    def update(pid, uid, start, seconds, result, kind="FULL_REFRESH", split_at=None):
        s = _t(start)
        e = s + timedelta(seconds=seconds)
        if split_at:                                     # an update over an hour boundary: two rows, result on last
            cut = _t(split_at)
            timeline.append((WS, pid, uid, kind, None, s, cut))
            timeline.append((WS, pid, uid, kind, result, cut, e))
        else:
            timeline.append((WS, pid, uid, kind, result, s, e))

    # day 18 pipeline, three post-merge runs on 10-22 and the daily run on 10-23: a fresh ID each time, two updates
    runs = [("p18a", "2026-10-22T16:05:00", (182, 96)), ("p18b", "2026-10-22T18:40:00", (171, 88)),
            ("p18c", "2026-10-22T21:10:00", (205, 101)), ("p18d", "2026-10-23T14:20:00", (166, 92))]
    for i, (pid, start, (first, second)) in enumerate(runs):
        s = _t(start)
        end = s + timedelta(seconds=first + second + 60)
        short_lived(pid, EXPECTED, start, end.isoformat(), null_name_on_delete=(i == 1))
        update(pid, pid + "-u1", s.isoformat(), first, "COMPLETED")
        update(pid, pid + "-u2", (s + timedelta(seconds=first + 20)).isoformat(), second, "COMPLETED")
    # a development pipeline renamed once; its first update failed, a retry completed, and one is still running
    pipelines.append((WS, "pdev", "scratch-store-params", _t("2026-10-20T10:00:00"), None))
    pipelines.append((WS, "pdev", "microfeatures-dev-store-params", _t("2026-10-20T10:30:00"), None))
    update("pdev", "pdev-u1", "2026-10-20T10:31:00", 74, "FAILED")
    update("pdev", "pdev-u2", "2026-10-20T10:40:00", 0, "COMPLETED", kind="VALIDATE")
    update("pdev", "pdev-u3", "2026-10-20T10:58:30", 3900, "COMPLETED", split_at="2026-10-20T11:00:00")
    update("pdev", "pdev-u4", "2026-10-23T14:50:00", 300, None, kind="REFRESH")
    # outside the report: an older day-18 test run (before the 7-day window) and someone else's pipeline
    short_lived("pold", EXPECTED, "2026-10-12T09:00:00", "2026-10-12T09:10:00")
    update("pold", "pold-u1", "2026-10-12T09:01:00", 150, "CANCELED")
    pipelines.append((WS, "pother", "grocery-nightly-load", _t("2026-10-01T00:00:00"), None))
    update("pother", "pother-u1", "2026-10-21T02:00:00", 640, "COMPLETED", kind="REFRESH")
    return pipelines, timeline


PIPELINES, TIMELINE = _fixture()


def report_sql() -> str:
    return (HERE / "report.sql").read_text()


def to_duckdb(sql: str, since: datetime, name_like: str) -> str:
    """The two dialect differences, for the local engine: timestampdiff and the system catalog (DuckDB has its own
    `system` catalog, so the copy lives in schema `lakeflow`); markers become literals."""
    sql = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"timestampdiff\(SECOND,\s*", "date_diff('second', ", sql)
    sql = sql.replace("system.lakeflow.", "lakeflow.")
    sql = sql.replace(":since", f"TIMESTAMP '{since:%Y-%m-%d %H:%M:%S}'")
    return sql.replace(":name_like", "'" + name_like.replace("'", "''") + "'")


def run_local(since: datetime = SINCE, name_like: str = NAME_LIKE) -> list[tuple]:
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE SCHEMA lakeflow")
    con.execute("CREATE TABLE lakeflow.pipelines (workspace_id VARCHAR, pipeline_id VARCHAR, name VARCHAR, "
                "change_time TIMESTAMP, delete_time TIMESTAMP)")
    con.execute("CREATE TABLE lakeflow.pipeline_update_timeline (workspace_id VARCHAR, pipeline_id VARCHAR, "
                "update_id VARCHAR, update_type VARCHAR, result_state VARCHAR, period_start_time TIMESTAMP, "
                "period_end_time TIMESTAMP)")
    con.executemany("INSERT INTO lakeflow.pipelines VALUES (?, ?, ?, ?, ?)", PIPELINES)
    con.executemany("INSERT INTO lakeflow.pipeline_update_timeline VALUES (?, ?, ?, ?, ?, ?, ?)", TIMELINE)
    return [_row(r) for r in con.execute(to_duckdb(report_sql(), since, name_like)).fetchall()]


def _row(r) -> tuple:
    return tuple([r[0]] + [None if v is None else int(v) for v in r[1:11]] + [r[11]])


# ---------- reference model (plain Python, used by the tests)
def pure_python(since: datetime = SINCE, prefix: str = NAME_LIKE.rstrip("%")) -> list[tuple]:
    latest_name, deleted = {}, {}
    for ws, pid, name, changed, gone in sorted(PIPELINES, key=lambda p: p[3]):
        if name is not None:
            latest_name[(ws, pid)] = name
        if gone is not None:
            deleted[(ws, pid)] = gone
    ups: dict[tuple, dict] = {}
    for ws, pid, uid, kind, result, start, end in TIMELINE:
        if start < since:
            continue
        u = ups.setdefault((ws, pid, uid), {"kind": kind, "result": None, "secs": 0, "end": end})
        u["secs"] += int((end - start).total_seconds())
        u["result"] = result or u["result"]
        u["end"] = max(u["end"], end)
    by_name: dict[str, list] = {}
    for (ws, pid, uid), u in ups.items():
        name = latest_name.get((ws, pid))
        if name and name.startswith(prefix):
            by_name.setdefault(name, []).append(((ws, pid), u))
    out = []
    for name in sorted(by_name):
        rows = by_name[name]
        ids = {k for k, _ in rows}
        secs = [u["secs"] for _, u in rows]
        res = [u["result"] for _, u in rows]
        last = max(rows, key=lambda r: r[1]["end"])[1]["result"] or "RUNNING"
        out.append((name, len(ids), len({k for k in ids if k in deleted}), len(rows), res.count("COMPLETED"),
                    res.count("FAILED"), res.count("CANCELED"), res.count(None),
                    sum(u["kind"] == "FULL_REFRESH" for _, u in rows), int(sum(secs) / len(secs) + 0.5), max(secs), last))
    return out


# ---------- Databricks: Statement Execution API on the serverless warehouse
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
        print("::error title=Databricks day 19::No SQL warehouse in the workspace")
        raise SystemExit(1)
    return next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])["id"]


def statement(wh: str, sql: str, params: list[dict]) -> tuple[str, list[list], str]:
    res = _api("POST", "/api/2.0/sql/statements", {"warehouse_id": wh, "statement": sql, "parameters": params,
                                                    "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    msg = res["status"].get("error", {}).get("message", "")[:500].replace("\n", " ")
    return res["status"]["state"], res.get("result", {}).get("data_array", []) or [], msg


def run_databricks() -> dict:
    wh = _warehouse()
    since = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) - timedelta(days=7)
    params = [{"name": "since", "value": since.strftime("%Y-%m-%d %H:%M:%S"), "type": "TIMESTAMP"},
              {"name": "name_like", "value": NAME_LIKE, "type": "STRING"}]
    state, rows, msg = statement(wh, report_sql(), params)
    if state != "SUCCEEDED":
        print(f"::error title=Databricks day 19 system tables not readable ({state})::{msg}")
        raise SystemExit(1)
    out = {"since": since.isoformat(), "lakeflow": [_row(r) for r in rows]}
    state, rows, msg = statement(wh, report_sql().replace("system.lakeflow.", "system.workflow."), params)
    out["workflow"] = [_row(r) for r in rows] if state == "SUCCEEDED" else None
    out["workflow_error"] = None if state == "SUCCEEDED" else msg[:200]
    return out


# ---------- checks and summary
def check(report: list[tuple]) -> list[str]:
    errors = []
    for r in report:
        d = dict(zip(COLUMNS, r))
        if d["completed"] + d["failed"] + d["canceled"] + d["running"] != d["updates"]:
            errors.append(f"{d['pipeline']}: outcomes don't add up to {d['updates']} updates")
        if d["deleted_ids"] > d["pipeline_ids"] or d["pipeline_ids"] > d["updates"]:
            errors.append(f"{d['pipeline']}: {d['deleted_ids']} deleted of {d['pipeline_ids']} IDs, {d['updates']} updates")
        if not d["pipeline"].startswith(NAME_LIKE.rstrip("%")):
            errors.append(f"a pipeline outside the filter was reported")
    day18 = [r for r in report if r[0] == EXPECTED]
    if not day18 or day18[0][3] < 2:
        errors.append(f"no updates of {EXPECTED} in the window (it runs twice in every daily run)")
    return errors


def compare_schemas(lakeflow: list[tuple], workflow: list[tuple] | None) -> list[str]:
    if workflow is None or [tuple(r) for r in workflow] == [tuple(r) for r in lakeflow]:
        return []
    return [f"system.workflow gives {len(workflow)} rows, system.lakeflow {len(lakeflow)}: "
            f"{sorted(set(map(tuple, workflow)) ^ set(map(tuple, lakeflow)))[:3]}"]


def summarise(report: list[tuple]) -> dict:
    rows = [dict(zip(COLUMNS, r)) for r in report]
    d18 = next((r for r in rows if r["pipeline"] == EXPECTED), None)
    head = (f"{len(rows)} pipelines, {sum(r['updates'] for r in rows)} updates in 7 days"
            + (f"; {EXPECTED}: {d18['updates']} updates across {d18['pipeline_ids']} short-lived IDs "
               f"({d18['deleted_ids']} deleted), {d18['completed']} completed, avg {d18['avg_seconds']}s" if d18 else ""))
    return {"pipelines": rows, "headline": head}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--databricks":
        cloud = run_databricks()
        errors = check(cloud["lakeflow"]) + compare_schemas(cloud["lakeflow"], cloud["workflow"])
        if cloud["workflow"] is None:
            print(f"::warning title=Databricks day 19::system.workflow not readable, schema comparison skipped: "
                  f"{cloud['workflow_error']}")
        if errors:
            print(f"::error title=Databricks day 19 report check failed::{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud["lakeflow"])
        print(f"::notice title=Databricks day 19::Pipeline run report from system.lakeflow (last 7 days): "
              f"{s['headline']}; system.workflow "
              f"{'identical' if cloud['workflow'] is not None else 'not checked'}; rows "
              f"{json.dumps([[r['pipeline'], r['updates'], r['completed'], r['failed'], r['avg_seconds']] for r in s['pipelines']])[:500]}")
    else:
        summary = summarise(run_local())
        (HERE / "result.json").write_text(json.dumps({
            "engine": "DuckDB on a synthetic copy of the two system tables (local) / system.lakeflow on the Free "
                      "Edition serverless warehouse (cloud)",
            "window": f"{SINCE:%Y-%m-%d %H:%M} to {NOW:%Y-%m-%d %H:%M} UTC (fixture)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
