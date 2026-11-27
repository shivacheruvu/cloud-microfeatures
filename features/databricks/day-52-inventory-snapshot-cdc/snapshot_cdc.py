"""Day 52 (Databricks): on-hand history from nightly whole-store inventory snapshots with SQL
AUTO CDC ... FROM SNAPSHOT ... WITH VERSION (Lakeflow Spark Declarative Pipelines, Databricks Runtime 18.3+).

A store exports its complete on-hand count every night of Thanksgiving week (snapshot versions 1-4). Nobody sends
change events: the pipeline has to work out what changed. Round 1 lands versions 1 and 2 before the first update
(one update must process both, in order); round 2 lands versions 3 and 4. Along the way: a new SKU appears
(whipped topping, v2), a SKU is unchanged for three nights (stuffing mix after v2), and a SKU vanishes from the
export (pie crusts pulled, v4), which must close its row as a delete.

  python snapshot_cdc.py --local       # pure Python diff + DuckDB reference after each round; writes result.json
  python snapshot_cdc.py --sql         # print pipeline.sql with the source filled in
  python snapshot_cdc.py --databricks  # GitHub Actions: append each round, run a pipeline update, compare to local

Cloud: a scratch schema gets the inventory_snapshots Delta table; a serverless pipeline (PREVIEW channel,
development mode) holds the snapshot flow. After each round's append, one update runs and inventory_history
(sku, on_hand, __START_AT, __END_AT) is read and compared with the local SCD type 2 history for that round. A refused
workspace (inactive Free Edition, compute disabled) or SQL snapshot CDC not being available on the channel is a
warning: the feature was then verified locally only. The pipeline, workspace folder and scratch schema are deleted
on exit. Results go out as ::notice / ::warning / ::error annotations, redacted.
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "cmf_day52_scratch"
PIPELINE_NAME = "cmf-day52-inventory-snapshot-cdc"
WS_DIR = "/Shared/cmf-day52"
TITLE = "Databricks day 52"

# snapshot_version -> {sku: on_hand}; each is the store's whole on-hand export that night
SNAPSHOTS = {
    1: {"turkey": 40, "cranberry": 120, "stuffing": 80, "pie_crust": 60},
    2: {"turkey": 25, "cranberry": 120, "stuffing": 55, "pie_crust": 60, "whipped": 90},   # whipped topping is new
    3: {"turkey": 25, "cranberry": 95, "stuffing": 55, "pie_crust": 60, "whipped": 90},    # only cranberry moves
    4: {"turkey": 6, "cranberry": 95, "stuffing": 55, "whipped": 40},                      # pie crusts pulled
}
ROUNDS = [[1, 2], [3, 4]]   # versions landed before each pipeline update


def snapshot_rows(v: int) -> list[tuple]:
    return [(v, sku, n) for sku, n in sorted(SNAPSHOTS[v].items())]


def landed(upto: int) -> list[int]:
    return [v for rnd in ROUNDS[:upto + 1] for v in rnd]


# ---------- local references: (sku, on_hand, __START_AT, __END_AT), __END_AT None while current
def reference(upto: int) -> list[tuple]:
    """Snapshot CDC as SCD type 2, in plain Python: diff each version with the previous one, in version order."""
    history, open_rows, prev = [], {}, {}
    for v in sorted(landed(upto)):
        snap = SNAPSHOTS[v]
        for sku in sorted(set(prev) | set(snap)):
            if sku in prev and (sku not in snap or snap[sku] != prev[sku]):
                open_rows.pop(sku)[3] = v                       # changed or deleted: close the open row
            if sku in snap and (sku not in prev or snap[sku] != prev[sku]):
                row = [sku, snap[sku], v, None]                 # inserted or changed: open a new row
                history.append(row)
                open_rows[sku] = row
        prev = snap
    return sorted(tuple(r) for r in history)


def duckdb_state(upto: int) -> list[tuple]:
    """The same history from SQL alone: runs of equal on_hand over consecutive versions, closed at the next change."""
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE inventory_snapshots (snapshot_version INT, sku VARCHAR, on_hand INT)")
    con.executemany("INSERT INTO inventory_snapshots VALUES (?, ?, ?)",
                    [r for v in landed(upto) for r in snapshot_rows(v)])
    return [tuple(r) for r in con.execute("""
        WITH versions AS (SELECT DISTINCT snapshot_version AS v FROM inventory_snapshots),
        grid AS (  -- every (sku, version) pair; on_hand NULL where the SKU is absent from that night's export
          SELECT k.sku, versions.v, s.on_hand
          FROM (SELECT DISTINCT sku FROM inventory_snapshots) k CROSS JOIN versions
          LEFT JOIN inventory_snapshots s ON s.sku = k.sku AND s.snapshot_version = versions.v),
        marked AS (
          SELECT *, CASE WHEN on_hand IS DISTINCT FROM lag(on_hand) OVER w THEN 1 ELSE 0 END AS starts
          FROM grid WINDOW w AS (PARTITION BY sku ORDER BY v)),
        runs AS (SELECT *, sum(starts) OVER (PARTITION BY sku ORDER BY v) AS run FROM marked),
        spans AS (
          SELECT sku, run, any_value(on_hand) AS on_hand, min(v) AS start_at, max(v) AS last_v
          FROM runs GROUP BY sku, run)
        SELECT s.sku, s.on_hand, s.start_at,
               (SELECT min(v) FROM versions WHERE v > s.last_v) AS end_at
        FROM spans s WHERE s.on_hand IS NOT NULL
        ORDER BY ALL""").fetchall()]


def run_local() -> list[list[tuple]]:
    states = []
    for i in range(len(ROUNDS)):
        ref = reference(i)
        if duckdb_state(i) != ref:
            raise SystemExit(f"round {i + 1}: DuckDB and Python references disagree")
        states.append(ref)
    return states


def pipeline_sql(source: str = "inventory_snapshots") -> str:
    return (HERE / "pipeline.sql").read_text().replace("{source}", source)


# ---------- Databricks: REST API (workspace files, pipelines) + Statement Execution API
def _redact(text: str) -> str:
    host = os.environ.get("DATABRICKS_HOST", "").removeprefix("https://").rstrip("/")
    if host:
        text = text.replace(host, "<host>")
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<user>", text)
    return re.sub(r"\b\d{7,}\b", "<id>", text)


class Refused(Exception):
    pass


def _api(method: str, path: str, body: dict | None = None, ok404: bool = False) -> dict:
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    if not host.startswith("https://"):
        host = "https://" + host
    req = urllib.request.Request(host + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if ok404 and e.code in (400, 404):
            return {}
        raise Refused(f"{method} {path.split('?')[0]} {e.code}: {e.read()[:300].decode(errors='replace')}")


def statement(wh: str, text: str) -> list[list]:
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": text, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")
        raise Refused(f"{res['status']['state']}: {msg[:450]}".replace("\n", " "))
    return res.get("result", {}).get("data_array", []) or []


def compute_refused(text: str) -> bool:
    t = text.lower()
    return ("feature_disabled" in t or "not active" in t or "cancelled" in t
            or "could not be processed by the warehouse" in t)


def beta_unavailable(text: str) -> bool:
    t = text.lower()
    return ("from snapshot" in t or "snapshot_version" in t or "auto_cdc_from_snapshot" in t
            or "apply_changes_from_snapshot" in t or "preview" in t)


def _pipeline_errors(pid: str) -> str:
    events = _api("GET", f"/api/2.0/pipelines/{pid}/events?max_results=50", ok404=True).get("events", [])
    msgs = [e.get("message", "") for e in events if e.get("level") in ("ERROR", "WARN")]
    for e in events:
        for ex in (e.get("error") or {}).get("exceptions", [])[:1]:
            msgs.append(ex.get("message", ""))
    return " | ".join(m.replace("\n", " ")[:250] for m in msgs[:4]) or "no error events"


def run_update(pid: str, rnd: int) -> None:
    uid = _api("POST", f"/api/2.0/pipelines/{pid}/updates", {"full_refresh": rnd == 0})["update_id"]
    state = "QUEUED"
    for _ in range(120):                                 # up to 20 minutes (serverless start)
        time.sleep(10)
        state = _api("GET", f"/api/2.0/pipelines/{pid}/updates/{uid}").get("update", {}).get("state", state)
        if state in ("COMPLETED", "FAILED", "CANCELED"):
            break
    if state != "COMPLETED":
        raise Refused(f"round {rnd + 1} update {state}: {_pipeline_errors(pid)}")


def _values(rows: list[tuple]) -> str:
    return ", ".join(f"({v}, '{k}', {n})" for v, k, n in rows)


def _int(x):
    return None if x is None else int(x)


def run_databricks() -> list[list[tuple]]:
    warehouses = _api("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
    if not warehouses:
        raise Refused("no SQL warehouse in the workspace")
    wh = next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])["id"]
    catalog = statement(wh, "SELECT current_catalog()")[0][0]
    target = f"{catalog}.{SCHEMA}"
    pid = None
    try:
        statement(wh, f"DROP SCHEMA IF EXISTS {target} CASCADE")
        statement(wh, f"CREATE SCHEMA {target}")
        statement(wh, f"CREATE TABLE {target}.inventory_snapshots (snapshot_version INT, sku STRING, on_hand INT)")
        _api("POST", "/api/2.0/workspace/mkdirs", {"path": WS_DIR})
        _api("POST", "/api/2.0/workspace/import", {
            "path": f"{WS_DIR}/pipeline.sql", "format": "RAW", "overwrite": True,
            "content": base64.b64encode(pipeline_sql(f"{target}.inventory_snapshots").encode()).decode()})
        pid = _api("POST", "/api/2.0/pipelines", {
            "name": PIPELINE_NAME, "serverless": True, "catalog": catalog, "schema": SCHEMA,
            "continuous": False, "development": True, "channel": "PREVIEW",
            "libraries": [{"file": {"path": f"/Workspace{WS_DIR}/pipeline.sql"}}],
        })["pipeline_id"]
        states = []
        for i, rnd in enumerate(ROUNDS):
            rows = [r for v in rnd for r in snapshot_rows(v)]
            statement(wh, f"INSERT INTO {target}.inventory_snapshots VALUES {_values(rows)}")
            run_update(pid, i)
            got = statement(wh, f"SELECT sku, on_hand, __START_AT, __END_AT FROM {target}.inventory_history "
                                "ORDER BY ALL")
            states.append(sorted((r[0], int(r[1]), _int(r[2]), _int(r[3])) for r in got))
        return states
    finally:  # leave nothing behind
        for step in (lambda: pid and _api("DELETE", f"/api/2.0/pipelines/{pid}", ok404=True),
                     lambda: _api("POST", "/api/2.0/workspace/delete", {"path": WS_DIR, "recursive": True},
                                  ok404=True),
                     lambda: statement(wh, f"DROP SCHEMA IF EXISTS {target} CASCADE")):
            try:
                step()
            except Exception as e:  # noqa: BLE001 - cleanup problems are reported, not fatal
                print(f"::warning title={TITLE} cleanup::{_redact(str(e))[:300]}")


# ---------- comparison and summary
def _key(r):
    return (r[0], r[1], r[2], -1 if r[3] is None else r[3])


def compare(cloud: list[list[tuple]], local: list[list[tuple]]) -> list[str]:
    errors = []
    for i, (c, l) in enumerate(zip(cloud, local)):
        c = sorted(map(tuple, c), key=_key)
        l = sorted(l, key=_key)
        if c != l:
            only_c, only_l = [r for r in c if r not in l], [r for r in l if r not in c]
            errors.append(f"round {i + 1}: {len(c)} rows vs {len(l)}; only cloud {only_c[:3]}, only local {only_l[:3]}")
    if len(cloud) != len(local):
        errors.append(f"{len(cloud)} rounds read, {len(local)} expected")
    return errors


def summarise(states: list[list[tuple]]) -> dict:
    rounds = []
    for i, st in enumerate(states):
        rounds.append({"round": i + 1, "versions_landed": ROUNDS[i],
                       "snapshot_rows_so_far": sum(len(SNAPSHOTS[v]) for v in landed(i)),
                       "history_rows": len(st), "current_rows": sum(1 for r in st if r[3] is None)})
    final = states[-1]
    current = {r[0]: r[1] for r in final if r[3] is None}
    closed_by_delete = sorted(r[0] for r in final if r[3] is not None and r[0] not in current)
    return {
        "snapshots": len(SNAPSHOTS), "skus": len({k for s in SNAPSHOTS.values() for k in s}), "rounds": rounds,
        "history": [{"sku": k, "on_hand": n, "start_at": a, "end_at": b} for k, n, a, b in final],
        "current_on_hand": current, "deleted_skus": closed_by_delete,
        "headline": f"{rounds[-1]['snapshot_rows_so_far']} snapshot rows over {len(SNAPSHOTS)} nights -> "
                    f"{len(final)} history rows, {len(current)} current; pie crusts closed as a delete at v4, "
                    f"stuffing kept one row through 3 unchanged nights",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(pipeline_sql())
    elif mode == "--databricks":
        local = run_local()
        try:
            cloud = run_databricks()
        except Refused as e:
            msg = _redact(str(e))
            if compute_refused(msg):
                print(f"::warning title={TITLE}::the workspace refused ({msg[:350]}); the account looks inactive, so "
                      "snapshot CDC was verified locally only (nothing was left behind)")
                raise SystemExit(0)
            if beta_unavailable(msg):
                print(f"::warning title={TITLE}::SQL AUTO CDC FROM SNAPSHOT is not available here ({msg[:350]}); it "
                      "needs Databricks Runtime 18.3+ on the pipeline channel. Verified locally only")
                raise SystemExit(0)
            print(f"::error title={TITLE} failed::{msg[:800]}")
            raise SystemExit(1)
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud)
        print(f"::notice title={TITLE}::AUTO CDC FROM SNAPSHOT on a serverless pipeline matched local after both "
              f"rounds: {s['headline']}; history rows per round {[r['history_rows'] for r in s['rounds']]}")
    else:
        states = run_local()
        summary = summarise(states)
        (HERE / "result.json").write_text(json.dumps({"engine": "pure Python diff + DuckDB reference (local) / "
                                                      "Lakeflow serverless pipeline with AUTO CDC FROM SNAPSHOT "
                                                      "SCD type 2 (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps({k: v for k, v in summary.items() if k != "history"}, indent=2))
