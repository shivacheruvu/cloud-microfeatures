"""Day 51 (Databricks): keep a shelf price list current from whole-store price files with a REPLACE USING flow
(Spark Declarative Pipelines, Beta, Databricks Runtime 18.2+).

Three stores send their full price file in three delivery rounds. Round 2 brings corrected files for S1 (one SKU
delisted, one price fixed) and S3 (one SKU added); round 3 brings S2's new file plus a LATE copy of S1's old file.
`FLOW REPLACE USING (store_id) SEQUENCE BY file_version` must leave exactly the newest file's rows per store:
the delisted SKU gone, the late old file ignored.

  python price_files.py --local       # pure Python + DuckDB reference after each round; writes result.json
  python price_files.py --sql         # print pipeline.sql with the source filled in
  python price_files.py --databricks  # GitHub Actions: append each round, run a pipeline update, compare to local

Cloud: a scratch schema gets the price_drops Delta table; a serverless pipeline (PREVIEW channel, development mode)
holds the REPLACE USING flow. After each round's append, one (incremental) update runs and shelf_prices is read and
compared with the local state for that round. A refused workspace (inactive Free Edition, compute disabled) or the
Beta not being available is a warning: the feature was then verified locally only. The pipeline, workspace folder
and scratch schema are deleted on exit. Results go out as ::notice / ::warning / ::error annotations, redacted.
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
SCHEMA = "cmf_day51_scratch"
PIPELINE_NAME = "cmf-day51-price-files-replace-using"
WS_DIR = "/Shared/cmf-day51"
TITLE = "Databricks day 51"

# (store_id, file_version) -> {sku: price_cents}; a file is the store's whole price list at that version
FILES = {
    ("S1", 1): {"0702": 389, "0711": 429, "0903": 479, "2201": 499, "4011": 59},
    ("S2", 1): {"0702": 399, "0711": 419, "0903": 489, "2201": 489, "4011": 62},
    ("S3", 1): {"0702": 379, "0711": 439, "2201": 509, "4011": 57},
    # round 2: corrected files. S1 delists 0903 (canned pumpkin recall) and fixes 2201 (499 -> 449); S3 adds 0903
    ("S1", 2): {"0702": 389, "0711": 429, "2201": 449, "4011": 59},
    ("S3", 2): {"0702": 379, "0711": 439, "0903": 469, "2201": 509, "4011": 57},
    # round 3: S2's new prices (holiday markdowns on 0702 and 0711)
    ("S2", 2): {"0702": 349, "0711": 379, "0903": 489, "2201": 489, "4011": 62},
}
ROUNDS = [
    [("S1", 1), ("S2", 1), ("S3", 1)],
    [("S1", 2), ("S3", 2)],
    [("S2", 2), ("S1", 1)],   # S1 v1 again: a late re-delivery of the old file; SEQUENCE BY must keep v2
]


def file_rows(key) -> list[tuple]:
    store, ver = key
    return [(store, ver, sku, price) for sku, price in sorted(FILES[key].items())]


def delivered(upto: int) -> list[tuple]:
    return [r for rnd in ROUNDS[:upto + 1] for key in rnd for r in file_rows(key)]


# ---------- local references
def reference(upto: int) -> list[tuple]:
    """REPLACE USING (store_id) SEQUENCE BY file_version, in plain Python: per store, the rows of its newest file."""
    rows = delivered(upto)
    newest = {}
    for store, ver, _, _ in rows:
        newest[store] = max(ver, newest.get(store, ver))
    return sorted(set(r for r in rows if r[1] == newest[r[0]]))


def latest_per_sku(upto: int) -> list[tuple]:
    """What keep-latest per (store, sku) (an SCD type 1 upsert) would give: delisted SKUs never go away."""
    best = {}
    for r in delivered(upto):
        k = (r[0], r[2])
        if k not in best or r[1] > best[k][1]:
            best[k] = r
    return sorted(best.values())


def duckdb_state(upto: int) -> list[tuple]:
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE price_drops (store_id VARCHAR, file_version INT, sku VARCHAR, price_cents INT)")
    con.executemany("INSERT INTO price_drops VALUES (?, ?, ?, ?)", delivered(upto))
    return [tuple(r) for r in con.execute(
        "SELECT DISTINCT store_id, file_version, sku, price_cents FROM price_drops "
        "QUALIFY file_version = max(file_version) OVER (PARTITION BY store_id) "
        "ORDER BY ALL").fetchall()]


def run_local() -> list[list[tuple]]:
    states = []
    for i in range(len(ROUNDS)):
        ref = reference(i)
        if duckdb_state(i) != ref:
            raise SystemExit(f"round {i + 1}: DuckDB and Python references disagree")
        states.append(ref)
    return states


def pipeline_sql(source: str = "price_drops") -> str:
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
    return "replace using" in t or "replace_using" in t or "preview" in t


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
    return ", ".join(f"('{s}', {v}, '{k}', {p})" for s, v, k, p in rows)


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
        statement(wh, f"CREATE TABLE {target}.price_drops "
                      "(store_id STRING, file_version INT, sku STRING, price_cents INT)")
        _api("POST", "/api/2.0/workspace/mkdirs", {"path": WS_DIR})
        _api("POST", "/api/2.0/workspace/import", {
            "path": f"{WS_DIR}/pipeline.sql", "format": "RAW", "overwrite": True,
            "content": base64.b64encode(pipeline_sql(f"{target}.price_drops").encode()).decode()})
        pid = _api("POST", "/api/2.0/pipelines", {
            "name": PIPELINE_NAME, "serverless": True, "catalog": catalog, "schema": SCHEMA,
            "continuous": False, "development": True, "channel": "PREVIEW",
            "libraries": [{"file": {"path": f"/Workspace{WS_DIR}/pipeline.sql"}}],
        })["pipeline_id"]
        states = []
        for i, rnd in enumerate(ROUNDS):
            rows = [r for key in rnd for r in file_rows(key)]
            statement(wh, f"INSERT INTO {target}.price_drops VALUES {_values(rows)}")
            run_update(pid, i)
            got = statement(wh, f"SELECT store_id, file_version, sku, price_cents FROM {target}.shelf_prices "
                                "ORDER BY ALL")
            states.append(sorted((r[0], int(r[1]), r[2], int(r[3])) for r in got))
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
def compare(cloud: list[list[tuple]], local: list[list[tuple]]) -> list[str]:
    errors = []
    for i, (c, l) in enumerate(zip(cloud, local)):
        if [tuple(r) for r in c] != l:
            only_c, only_l = sorted(set(map(tuple, c)) - set(l)), sorted(set(l) - set(map(tuple, c)))
            errors.append(f"round {i + 1}: {len(c)} rows vs {len(l)}; only cloud {only_c[:3]}, only local {only_l[:3]}")
    if len(cloud) != len(local):
        errors.append(f"{len(cloud)} rounds read, {len(local)} expected")
    return errors


def summarise(states: list[list[tuple]]) -> dict:
    rounds = []
    for i, st in enumerate(states):
        per_store = {}
        for s, v, _, _ in st:
            per_store.setdefault(s, {"file_version": v, "skus": 0})["skus"] += 1
        rounds.append({"round": i + 1, "files_delivered": [f"{s} v{v}" for s, v in ROUNDS[i]],
                       "rows_delivered_so_far": len(delivered(i)), "shelf_rows": len(st),
                       "keep_latest_per_sku_rows": len(latest_per_sku(i)), "stores": per_store})
    final = {(s, k): p for s, _, k, p in states[-1]}
    return {
        "stores": 3, "rounds": rounds,
        "final_prices": [{"store_id": s, "sku": k, "price_cents": p} for (s, k), p in sorted(final.items())],
        "s1_0903_delisted": ("S1", "0903") not in final, "late_s1_v1_ignored": final.get(("S1", "2201")) == 449,
        "headline": f"{len(delivered(len(ROUNDS) - 1))} price rows in 6 files -> {len(states[-1])} shelf prices; "
                    f"recalled SKU gone from S1, late old S1 file ignored (keep-latest per SKU would keep "
                    f"{len(latest_per_sku(len(ROUNDS) - 1))} rows)",
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
                      "the REPLACE USING flow was verified locally only (nothing was left behind)")
                raise SystemExit(0)
            if beta_unavailable(msg):
                print(f"::warning title={TITLE}::REPLACE USING flows are not available here ({msg[:350]}); the Beta "
                      "may need turning on in the workspace Previews page. Verified locally only")
                raise SystemExit(0)
            print(f"::error title={TITLE} failed::{msg[:800]}")
            raise SystemExit(1)
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud)
        print(f"::notice title={TITLE}::REPLACE USING flow on a serverless pipeline matched local after all 3 rounds: "
              f"{s['headline']}; shelf rows per round {[r['shelf_rows'] for r in s['rounds']]}")
    else:
        states = run_local()
        summary = summarise(states)
        (HERE / "result.json").write_text(json.dumps({"engine": "pure Python + DuckDB reference (local) / Lakeflow "
                                                      "serverless pipeline with a REPLACE USING flow (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps({k: v for k, v in summary.items() if k != "final_prices"}, indent=2))
