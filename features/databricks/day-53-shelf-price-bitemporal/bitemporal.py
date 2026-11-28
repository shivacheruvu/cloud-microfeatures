"""Day 53 (Databricks): shelf-price history on two time axes with **bitemporal AUTO CDC**
(`STORED AS BITEMPORAL` with `SYSTEM SEQUENCE BY`, Beta, Lakeflow Spark Declarative Pipelines, DBR 17.3+).

A store's price feed sends price changes in numbered batches during Thanksgiving week. Each change says which
November day the price takes effect on the shelf (business time, SEQUENCE BY effective_day); the batch number says
when we learned it (system time, SYSTEM SEQUENCE BY feed_batch). Some batches are late or restate the past:
  batch 3 reports a turkey price change on the 22nd that the feed missed (out of order in business time);
  batch 4 restates cranberry sauce: the price since the 20th was 2.79, not 2.99 (same business time, new belief);
  batch 5 cancels the stuffing promo of the 23rd (restated back to 3.49) and adds pie crusts from the 25th.
Round 1 lands batches 1-2 before the first pipeline update, round 2 lands batches 3-5.

  python bitemporal.py --local       # Python bitemporal history + two DuckDB checks per round; writes result.json
  python bitemporal.py --sql         # print pipeline.sql with the source filled in
  python bitemporal.py --databricks  # GitHub Actions: append each round, run a pipeline update, compare to local

The comparison is on **as-of answers**, not raw rows: for every SKU, every effective day 20-26 and every feed batch
seen so far, "what price did we believe, as of batch S, was on the shelf on day B?" read from the target with the
two-axis filter from Databricks' pipelines skill. How the engine splits rows can differ from the local row builder
without changing a single answer, so answers are what must match. Locally, the Python row builder, a DuckDB query of
those rows with the same filter, and a DuckDB query straight from the raw feed must all agree.

Cloud: a scratch schema gets the price_feed Delta table; a serverless pipeline (PREVIEW channel, development mode)
holds the flow. A refused workspace (inactive Free Edition, compute disabled) or bitemporal CDC not being available
is a warning: the feature was then verified locally only. Pipeline, workspace folder and schema are deleted on exit.
Results go out as ::notice / ::warning / ::error annotations, redacted.
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
SCHEMA = "cmf_day53_scratch"
PIPELINE_NAME = "cmf-day53-shelf-price-bitemporal"
WS_DIR = "/Shared/cmf-day53"
TITLE = "Databricks day 53"
DAYS = range(20, 27)          # effective days (November) asked about

# (feed_batch, sku, price, effective_day): one row per price change the feed reports
FEED = [
    (1, "turkey", 1.49, 20), (1, "cranberry", 2.99, 20), (1, "stuffing", 3.49, 20),
    (2, "turkey", 0.99, 24), (2, "stuffing", 2.99, 23),                  # promos announced ahead of time
    (3, "turkey", 1.29, 22),                                             # late: a change on the 22nd the feed missed
    (4, "cranberry", 2.79, 20),                                          # restated: it was 2.79 since the 20th
    (5, "stuffing", 3.49, 23), (5, "pie_crust", 4.99, 25),               # promo cancelled; new SKU
]
ROUNDS = [[1, 2], [3, 4, 5]]   # feed batches landed before each pipeline update


def landed(upto: int) -> list[int]:
    return [b for rnd in ROUNDS[:upto + 1] for b in rnd]


def feed_rows(batches) -> list[tuple]:
    return [r for r in FEED if r[0] in set(batches)]


# ---------- local references
def belief(batch: int) -> list[tuple]:
    """SCD type 2 on business time, from what the feed had told us by `batch`: (sku, price, start_day, end_day).
    For the same SKU and effective day, the later batch wins (a restatement)."""
    latest: dict[tuple, tuple] = {}
    for b, sku, price, day in sorted(FEED):
        if b <= batch:
            latest[(sku, day)] = (price, b)
    out = []
    for sku in sorted({k[0] for k in latest}):
        days = sorted(d for s, d in latest if s == sku)
        for i, d in enumerate(days):
            out.append((sku, latest[(sku, d)][0], d, days[i + 1] if i + 1 < len(days) else None))
    # adjacent spans with the same price are one version
    merged: list[list] = []
    for r in out:
        if merged and merged[-1][0] == r[0] and merged[-1][1] == r[1] and merged[-1][3] == r[2]:
            merged[-1][3] = r[3]
        else:
            merged.append(list(r))
    return [tuple(r) for r in merged]


def bitemporal_rows(upto: int) -> list[tuple]:
    """(sku, price, __START_AT, __END_AT, __SYSTEM_START_AT, __SYSTEM_END_AT): each business-time version, valid
    over the feed batches during which we believed it; None = still open."""
    batches = sorted(landed(upto))
    rows, open_rows = [], {}
    for b in batches:
        now = set(belief(b))
        for r in list(open_rows):
            if r not in now:
                open_rows.pop(r)[5] = b                 # belief changed: close it in system time, keep the row
        for r in sorted(now - set(open_rows)):
            row = [*r, b, None]
            rows.append(row)
            open_rows[r] = row
    return sorted((tuple(r) for r in rows), key=_key)


def _key(r):
    return tuple(-1 if x is None else x for x in r)


def as_of(rows: list[tuple], sku: str, day: int, batch: int):
    """The two-axis filter: business range contains `day`, system range contains `batch`."""
    hits = [r[1] for r in rows if r[0] == sku and r[2] <= day and (r[3] is None or r[3] > day)
            and r[4] <= batch and (r[5] is None or r[5] > batch)]
    if len(hits) > 1:
        raise ValueError(f"{sku} day {day} batch {batch}: {len(hits)} versions")
    return hits[0] if hits else None


def grid(rows: list[tuple], upto: int) -> dict[str, float | None]:
    skus = sorted({r[1] for r in feed_rows(landed(upto))})
    return {f"{s}|{d}|{b}": as_of(rows, s, d, b) for s in skus for d in DAYS for b in sorted(landed(upto))}


def _duck(upto: int):
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE price_feed (feed_batch INT, sku VARCHAR, price DECIMAL(6,2), effective_day INT)")
    con.executemany("INSERT INTO price_feed VALUES (?, ?, ?, ?)", feed_rows(landed(upto)))
    con.execute("CREATE TABLE shelf_prices (sku VARCHAR, price DECIMAL(6,2), __START_AT INT, __END_AT INT, "
                "__SYSTEM_START_AT INT, __SYSTEM_END_AT INT)")
    con.executemany("INSERT INTO shelf_prices VALUES (?, ?, ?, ?, ?, ?)", bitemporal_rows(upto))
    con.execute(f"CREATE TABLE days AS SELECT range AS d FROM range({DAYS.start}, {DAYS.stop})")
    return con


AS_OF_SQL = """
SELECT k.sku, days.d, b.feed_batch, any_value(p.price) AS price, count(p.price) AS versions
FROM (SELECT DISTINCT sku FROM price_feed) k CROSS JOIN days CROSS JOIN (SELECT DISTINCT feed_batch FROM price_feed) b
LEFT JOIN shelf_prices p
  ON p.sku = k.sku
 AND p.__START_AT <= days.d AND (p.__END_AT > days.d OR p.__END_AT IS NULL)
 AND p.__SYSTEM_START_AT <= b.feed_batch AND (p.__SYSTEM_END_AT > b.feed_batch OR p.__SYSTEM_END_AT IS NULL)
GROUP BY ALL ORDER BY ALL"""

FROM_FEED_SQL = """
-- straight from the raw feed: the latest-effective change on or before day d, among batches up to b,
-- with a restatement of the same effective day won by the later batch
SELECT k.sku, days.d, b.feed_batch,
       (SELECT f.price FROM price_feed f
         WHERE f.sku = k.sku AND f.effective_day <= days.d AND f.feed_batch <= b.feed_batch
         ORDER BY f.effective_day DESC, f.feed_batch DESC LIMIT 1) AS price
FROM (SELECT DISTINCT sku FROM price_feed) k CROSS JOIN days CROSS JOIN (SELECT DISTINCT feed_batch FROM price_feed) b
ORDER BY ALL"""


def _price(x):
    return None if x is None else round(float(x), 2)


def duckdb_grids(upto: int) -> tuple[dict, dict]:
    con = _duck(upto)
    rows = con.execute(AS_OF_SQL).fetchall()
    if any(r[4] > 1 for r in rows):
        raise SystemExit(f"round {upto + 1}: more than one version matched an as-of point")
    via_rows = {f"{s}|{d}|{b}": _price(p) for s, d, b, p, _ in rows}
    via_feed = {f"{s}|{d}|{b}": _price(p) for s, d, b, p in con.execute(FROM_FEED_SQL).fetchall()}
    return via_rows, via_feed


def run_local() -> list[dict]:
    grids = []
    for i in range(len(ROUNDS)):
        g = grid(bitemporal_rows(i), i)
        via_rows, via_feed = duckdb_grids(i)
        if not (g == via_rows == via_feed):
            raise SystemExit(f"round {i + 1}: Python rows, DuckDB as-of and DuckDB from-feed disagree")
        grids.append(g)
    return grids


def pipeline_sql(source: str = "price_feed") -> str:
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
    return "bitemporal" in t or "system sequence" in t or "system_sequence" in t or "preview" in t


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
    return ", ".join(f"({b}, '{k}', {p:.2f}, {d})" for b, k, p, d in rows)


def _int(x):
    return None if x is None else int(x)


def run_databricks() -> list[dict]:
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
        statement(wh, f"CREATE TABLE {target}.price_feed "
                      "(feed_batch INT, sku STRING, price DECIMAL(6,2), effective_day INT)")
        _api("POST", "/api/2.0/workspace/mkdirs", {"path": WS_DIR})
        _api("POST", "/api/2.0/workspace/import", {
            "path": f"{WS_DIR}/pipeline.sql", "format": "RAW", "overwrite": True,
            "content": base64.b64encode(pipeline_sql(f"{target}.price_feed").encode()).decode()})
        pid = _api("POST", "/api/2.0/pipelines", {
            "name": PIPELINE_NAME, "serverless": True, "catalog": catalog, "schema": SCHEMA,
            "continuous": False, "development": True, "channel": "PREVIEW",
            "libraries": [{"file": {"path": f"/Workspace{WS_DIR}/pipeline.sql"}}],
        })["pipeline_id"]
        grids = []
        for i, rnd in enumerate(ROUNDS):
            statement(wh, f"INSERT INTO {target}.price_feed VALUES {_values(feed_rows(rnd))}")
            run_update(pid, i)
            got = statement(wh, "SELECT sku, price, __START_AT, __END_AT, __SYSTEM_START_AT, __SYSTEM_END_AT "
                                f"FROM {target}.shelf_prices ORDER BY ALL")
            rows = [(r[0], _price(r[1]), _int(r[2]), _int(r[3]), _int(r[4]), _int(r[5])) for r in got]
            grids.append({"rows": len(rows), "grid": grid(rows, i)})
        return grids
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
def compare(cloud: list[dict], local: list[dict]) -> list[str]:
    errors = []
    for i, (c, l) in enumerate(zip(cloud, local)):
        diff = sorted(k for k in l if c.get(k) != l[k])
        if diff or set(c) != set(l):
            errors.append(f"round {i + 1}: {len(diff)} of {len(l)} as-of answers differ, e.g. "
                          + ", ".join(f"{k}: cloud {c.get(k)} local {l[k]}" for k in diff[:3]))
    if len(cloud) != len(local):
        errors.append(f"{len(cloud)} rounds read, {len(local)} expected")
    return errors


def summarise(grids: list[dict]) -> dict:
    final_rows = bitemporal_rows(len(ROUNDS) - 1)
    last = max(landed(len(ROUNDS) - 1))
    final = grids[-1]
    # answers where what we believed at the time differs from what we know now (the case SCD type 2 can't answer)
    revised = sorted(k for k, v in final.items()
                     if v is not None and v != final[f"{k.rsplit('|', 1)[0]}|{last}"])
    current = [[r[0], r[1], r[2], r[3]] for r in final_rows if r[5] is None]   # sku, price, from day, to day
    return {
        "feed_rows": len(FEED), "feed_batches": len({r[0] for r in FEED}), "skus": len({r[1] for r in FEED}),
        "rounds": [{"round": i + 1, "batches_landed": ROUNDS[i],
                    "bitemporal_rows": len(bitemporal_rows(i)), "as_of_answers": len(g)}
                   for i, g in enumerate(grids)],
        "rows": [list(r) for r in final_rows],
        "current_view_rows": sum(1 for r in final_rows if r[5] is None),
        "closed_beliefs": sum(1 for r in final_rows if r[5] is not None),
        "as_of_answers": len(final), "answers_revised_later": len(revised),
        "examples": {
            "turkey on the 22nd, as of batch 2": final["turkey|22|2"],
            "turkey on the 22nd, as of batch 5": final["turkey|22|5"],
            "cranberry on the 21st, as of batch 3": final["cranberry|21|3"],
            "cranberry on the 21st, as of batch 5": final["cranberry|21|5"],
            "stuffing on the 23rd, as of batch 4": final["stuffing|23|4"],
            "stuffing on the 23rd, as of batch 5": final["stuffing|23|5"],
        },
        "current_view": current,
        "headline": f"{len(FEED)} price changes in 5 feed batches -> {len(final_rows)} bitemporal rows "
                    f"({sum(1 for r in final_rows if r[5] is None)} in the current view); {len(revised)} of "
                    f"{len(final)} as-of answers were later revised (turkey on the 22nd: 1.49 as of batch 2, "
                    f"1.29 now)",
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
                      "bitemporal CDC was verified locally only (nothing was left behind)")
                raise SystemExit(0)
            if beta_unavailable(msg):
                print(f"::warning title={TITLE}::STORED AS BITEMPORAL is not available here ({msg[:350]}); it is "
                      "Beta (DBR 17.3+) and may need enabling on the Previews page. Verified locally only")
                raise SystemExit(0)
            print(f"::error title={TITLE} failed::{msg[:800]}")
            raise SystemExit(1)
        errors = compare([c["grid"] for c in cloud], local)
        if errors:
            print(f"::error title=Databricks result differs from local::{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(local)
        print(f"::notice title={TITLE}::bitemporal AUTO CDC on a serverless pipeline matched local on every as-of "
              f"answer after both rounds ({[len(g) for g in local]} answers; cloud rows per round "
              f"{[c['rows'] for c in cloud]}): {s['headline']}")
    else:
        grids = run_local()
        summary = summarise(grids)
        (HERE / "result.json").write_text(json.dumps({"engine": "Python bitemporal rows + two DuckDB checks (local) / "
                                                      "Lakeflow serverless pipeline with AUTO CDC STORED AS "
                                                      "BITEMPORAL (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
