"""Day 18 (Databricks): one store-sales pipeline, any store. The pipeline's SQL reads `:store_id` and `:start_date`
as named parameter markers (Lakeflow pipeline parameters, GA 2026-10-12). The pipeline keeps S1 from 2026-10-12 as
its saved defaults; a second update overrides them for that run only (S2 from 2026-10-15) without editing the
pipeline or its SQL.

  python store_pipeline.py --local        # DuckDB runs the same pipeline.sql with each run's values; writes result.json
  python store_pipeline.py --render       # rewrite pipeline.sql from SCANS (the test checks it is up to date)
  python store_pipeline.py --databricks   # Free Edition: upload pipeline.sql, create a serverless pipeline with
                                          # defaults, run it twice (defaults, then a per-run override), read
                                          # store_daily after each run, compare with local, delete everything

The cloud check (errors):
  * a pipeline update that doesn't complete
  * store_daily after either run differs from the local run with the same parameter values
  * the override changed the pipeline's saved defaults
Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed about
the workspace except the results.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "microfeatures_day18"
PIPELINE_NAME = "microfeatures-day18-store-params"
WS_DIR = "/Shared/microfeatures-day18"

STORES = ["S1", "S2", "S3"]
SKUS = [("4011", "bananas (lb)", 59), ("0702", "whole milk 1gal", 389), ("0711", "large eggs 12ct", 429),
        ("2201", "sourdough loaf", 499), ("0903", "oat milk 64oz", 479)]
DAYS = [date(2026, 10, 12) + timedelta(days=i) for i in range(7)]   # Mon 12 Oct .. Sun 18 Oct


def _qty(si: int, di: int, ki: int) -> int:
    return (si * 7 + di * 3 + ki * 5 + (di * ki) % 4) % 9            # 0 = not sold that day


SCANS = [(s, d.isoformat(), sku, _qty(si, di, ki), cents)
         for si, s in enumerate(STORES) for di, d in enumerate(DAYS) for ki, (sku, _, cents) in enumerate(SKUS)
         if _qty(si, di, ki) > 0]

DEFAULTS = {"store_id": "S1", "start_date": "2026-10-12"}            # saved on the pipeline
OVERRIDE = {"store_id": "S2", "start_date": "2026-10-15"}            # sent with one update only
RUNS = [("defaults", DEFAULTS, None), ("override", {**DEFAULTS, **OVERRIDE}, OVERRIDE)]


# ---------- the pipeline source (one SQL file, rendered from SCANS)
def render_pipeline_sql() -> str:
    rows = ",\n    ".join(f"('{s}', DATE'{d}', '{k}', {q}, {c})" for s, d, k, q, c in SCANS)
    return f"""-- Day 18 Databricks: store sales pipeline. :store_id and :start_date are pipeline parameters
-- (Lakeflow pipeline parameters, GA 2026-10-12). Defaults live in the pipeline settings; a run can override them.

CREATE OR REFRESH MATERIALIZED VIEW scans
COMMENT 'ANTeater-style scan lines for three stores, one week (synthetic)'
AS SELECT * FROM (VALUES
    {rows}
) AS t(store_id, scan_date, sku, qty, unit_cents);

CREATE OR REFRESH MATERIALIZED VIEW store_daily
COMMENT 'Daily sales for the store and start date passed as pipeline parameters'
AS SELECT
  store_id,
  scan_date,
  count(*)               AS scan_lines,
  sum(qty)               AS units,
  sum(qty * unit_cents)  AS sales_cents
FROM scans
WHERE store_id = :store_id
  AND scan_date >= CAST(:start_date AS DATE)
GROUP BY store_id, scan_date;
"""


def pipeline_sql() -> str:
    return (HERE / "pipeline.sql").read_text()


def parameters_used(sql: str) -> list[str]:
    body = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
    body = re.sub(r"'[^']*'", "''", body)                             # ignore text inside string literals
    body = re.sub(r"--[^\n]*", "", body)                              # and trailing comments
    return sorted(set(re.findall(r"(?<![:\w]):([A-Za-z_][\w.-]*)", body)))


def bind(sql: str, params: dict[str, str]) -> str:
    """What the pipeline does with :name markers, for the local engine: every one must have a value."""
    missing = [p for p in parameters_used(sql) if p not in params]
    if missing:
        raise KeyError(f"no value for pipeline parameter(s) {missing}")
    lines = []
    for l in sql.splitlines():
        if not l.lstrip().startswith("--"):
            l = re.sub(r"(?<![:\w]):([A-Za-z_][\w.-]*)", lambda m: "'" + params[m.group(1)].replace("'", "''") + "'", l)
        lines.append(l)
    return "\n".join(lines)


# ---------- reference model (plain Python, used by the tests)
def pure_python(params: dict[str, str]) -> list[tuple]:
    out: dict[str, list[int]] = {}
    for s, d, _, q, c in SCANS:
        if s == params["store_id"] and d >= params["start_date"]:
            row = out.setdefault(d, [0, 0, 0])
            row[0] += 1; row[1] += q; row[2] += q * c
    return [(params["store_id"], d, *v) for d, v in sorted(out.items())]


# ---------- local: DuckDB runs pipeline.sql (materialized views become tables)
def run_pipeline_local(params: dict[str, str]) -> list[tuple]:
    import duckdb
    con = duckdb.connect()
    sql = bind(pipeline_sql(), params).replace("CREATE OR REFRESH MATERIALIZED VIEW", "CREATE OR REPLACE TABLE")
    sql = re.sub(r"\nCOMMENT '[^']*'", "", sql)
    sql = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
    for stmt in (s for s in sql.split(";") if s.strip()):
        con.execute(stmt)
    return [(r[0], r[1].isoformat(), int(r[2]), int(r[3]), int(r[4])) for r in
            con.execute("SELECT store_id, scan_date, scan_lines, units, sales_cents FROM store_daily "
                        "ORDER BY scan_date").fetchall()]


def run_local() -> dict:
    return {name: run_pipeline_local(params) for name, params, _ in RUNS}


# ---------- Databricks: REST API (workspace files, pipelines) + Statement Execution API
def _api(method: str, path: str, body: dict | None = None, ok404: bool = False) -> dict:
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    req = urllib.request.Request(host + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if ok404 and e.code in (400, 404):
            return {}
        print(f"::error title=Databricks API {e.code}::{method} {path.split('?')[0]}: {e.read()[:300]!r}")
        raise SystemExit(1)


def _warehouse() -> str:
    warehouses = _api("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
    if not warehouses:
        print("::error title=Databricks day 18::No SQL warehouse in the workspace")
        raise SystemExit(1)
    return next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])["id"]


def statement(wh: str, sql: str, name: str, fail: bool = True) -> list[list]:
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")[:500].replace("\n", " ")
        print(f"::{'error' if fail else 'warning'} title=Databricks day 18 {name} {res['status']['state']}::{msg}")
        if fail:
            raise SystemExit(1)
    return res.get("result", {}).get("data_array", []) or []


def _delete_old_pipelines() -> None:
    q = urllib.parse.quote(f"name LIKE '{PIPELINE_NAME}'")
    for p in _api("GET", f"/api/2.0/pipelines?filter={q}&max_results=25", ok404=True).get("statuses", []):
        if p.get("name") == PIPELINE_NAME:
            _api("DELETE", f"/api/2.0/pipelines/{p['pipeline_id']}", ok404=True)


def _pipeline_errors(pid: str) -> str:
    events = _api("GET", f"/api/2.0/pipelines/{pid}/events?max_results=50", ok404=True).get("events", [])
    msgs = [e.get("message", "") for e in events if e.get("level") in ("ERROR", "WARN")]
    for e in events:
        for ex in (e.get("error") or {}).get("exceptions", [])[:1]:
            msgs.append(ex.get("message", ""))
    return " | ".join(m.replace("\n", " ")[:250] for m in msgs[:4]) or "no error events"


def run_update(pid: str, name: str, override: dict | None) -> dict:
    body = {"full_refresh": True}
    if override:
        body["parameters"] = override                    # this run only; the pipeline's saved defaults stay
    uid = _api("POST", f"/api/2.0/pipelines/{pid}/updates", body)["update_id"]
    state = "QUEUED"
    for _ in range(120):                                 # up to 20 minutes (serverless start + two small views)
        time.sleep(10)
        upd = _api("GET", f"/api/2.0/pipelines/{pid}/updates/{uid}").get("update", {})
        state = upd.get("state", state)
        if state in ("COMPLETED", "FAILED", "CANCELED"):
            break
    if state != "COMPLETED":
        print(f"::error title=Databricks day 18 {name} update {state}::{_pipeline_errors(pid)}")
        raise SystemExit(1)
    return upd


def run_databricks() -> dict:
    wh = _warehouse()
    catalog = statement(wh, "SELECT current_catalog()", "catalog")[0][0]
    target = f"{catalog}.{SCHEMA}"
    _delete_old_pipelines()
    statement(wh, f"DROP SCHEMA IF EXISTS {target} CASCADE", "drop old schema", fail=False)
    statement(wh, f"CREATE SCHEMA IF NOT EXISTS {target}", "create schema")
    pid = None
    try:
        _api("POST", "/api/2.0/workspace/mkdirs", {"path": WS_DIR})
        _api("POST", "/api/2.0/workspace/import", {
            "path": f"{WS_DIR}/pipeline.sql", "format": "RAW", "overwrite": True,
            "content": base64.b64encode(pipeline_sql().encode()).decode()})
        pid = _api("POST", "/api/2.0/pipelines", {
            "name": PIPELINE_NAME, "serverless": True, "catalog": catalog, "schema": SCHEMA,
            "continuous": False, "development": True, "channel": "CURRENT",
            "libraries": [{"file": {"path": f"/Workspace{WS_DIR}/pipeline.sql"}}],
            "parameters": DEFAULTS,
        })["pipeline_id"]
        out = {"tables": {}, "recorded": {}}
        for name, params, override in RUNS:
            upd = run_update(pid, name, override)
            out["recorded"][name] = upd.get("parameters")
            rows = statement(wh, f"SELECT store_id, scan_date, scan_lines, units, sales_cents FROM {target}.store_daily "
                                 "ORDER BY scan_date", f"read store_daily ({name})")
            out["tables"][name] = [(r[0], str(r[1]), int(r[2]), int(r[3]), int(r[4])) for r in rows]
        spec = _api("GET", f"/api/2.0/pipelines/{pid}").get("spec", {})
        out["saved_defaults_after"] = spec.get("parameters")
        return out
    finally:  # leave nothing behind
        if pid:
            _api("DELETE", f"/api/2.0/pipelines/{pid}", ok404=True)
        _api("POST", "/api/2.0/workspace/delete", {"path": WS_DIR, "recursive": True}, ok404=True)
        for _ in range(3):  # the pipeline's views go with the pipeline; the schema may need a moment
            statement(wh, f"DROP SCHEMA IF EXISTS {target} CASCADE", "drop schema", fail=False)
            left = statement(wh, f"SELECT count(*) FROM {catalog}.information_schema.schemata WHERE schema_name = "
                                 f"'{SCHEMA}'", "schema check", fail=False)
            if left and int(left[0][0]) == 0:
                break
            time.sleep(15)


# ---------- comparison and summary
def compare(cloud: dict, local: dict) -> list[str]:
    errors = []
    for name, _, _ in RUNS:
        c, l = [tuple(r) for r in cloud["tables"].get(name, [])], [tuple(r) for r in local[name]]
        if c != l:
            only_c, only_l = sorted(set(c) - set(l)), sorted(set(l) - set(c))
            errors.append(f"{name}: {len(c)} rows vs {len(l)}; only cloud {only_c[:3]}, only local {only_l[:3]}")
    saved = cloud.get("saved_defaults_after")
    if saved is not None and saved != DEFAULTS:
        errors.append(f"saved defaults changed by the override: {saved}")
    return errors


def summarise(tables: dict) -> dict:
    runs = []
    for name, params, override in RUNS:
        rows = tables[name]
        runs.append({"run": name, "store_id": params["store_id"], "start_date": params["start_date"],
                     "parameters_sent": override or {}, "days": len(rows),
                     "units": sum(r[3] for r in rows), "sales_cents": sum(r[4] for r in rows),
                     "best_day": max(rows, key=lambda r: r[4])[1] if rows else None,
                     "daily": [{"date": r[1], "lines": r[2], "units": r[3], "sales_cents": r[4]} for r in rows]})
    d, o = runs
    return {
        "scan_lines_in_source": len(SCANS), "stores": len(STORES),
        "parameters": parameters_used(pipeline_sql()), "saved_defaults": DEFAULTS,
        "runs": runs,
        "headline": f"one pipeline, two stores: {d['store_id']} from {d['start_date'][5:]} (defaults) "
                    f"${d['sales_cents'] / 100:,.2f} over {d['days']} days; {o['store_id']} from "
                    f"{o['start_date'][5:]} (per-run override) ${o['sales_cents'] / 100:,.2f} over {o['days']} days",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--render":
        (HERE / "pipeline.sql").write_text(render_pipeline_sql())
    elif mode == "--databricks":
        local = run_local()
        cloud = run_databricks()
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{len(errors)} difference(s): "
                  f"{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud["tables"])
        print("::notice title=Databricks day 18::Serverless pipeline ran twice with pipeline parameters and matched "
              f"local: {s['headline']}; parameters recorded on the updates {json.dumps(cloud['recorded'])}; "
              f"saved defaults after the override {json.dumps(cloud['saved_defaults_after'])}")
    else:
        summary = summarise(run_local())
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB on the same pipeline.sql (local) / Lakeflow "
                                                      "serverless pipeline with parameters (cloud)", **summary},
                                                     indent=2) + "\n")
        print(json.dumps({k: v for k, v in summary.items() if k != "runs"}, indent=2))
