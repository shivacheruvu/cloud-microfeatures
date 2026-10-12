"""Day 7 (Databricks): which shelf tags to reprint? Read a price table's row-level changes with automatic change data
feed (GA, Databricks SQL 2026.36): row tracking on, no `delta.enableChangeDataFeed`, and `table_changes()` still works.

  python shelf_changes.py --local        # DuckDB: the same four DML steps, changes derived by diffing snapshots;
                                         # writes result.json
  python shelf_changes.py --databricks   # serverless SQL warehouse via the Statement Execution API: scratch schema,
                                         # four steps, table_changes(), compare with local, drop everything

The cloud check:
  * errors:   the change data feed property is set (it must not be: that would be legacy CDF); a step's net effect per
              shelf item (price before -> after) differs from local; the final table differs from local
  * warnings: the exact change rows differ (e.g. an update shown as delete + insert) while the net effect agrees
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
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "microfeatures_day07"
COLS = ["store", "barcode", "product", "price_cents"]
STEPS = ["step1_load", "step2_supplier_increase", "step3_discontinue", "step4_weekly_feed"]
TYPE_ORDER = {"delete": 0, "insert": 1, "update_preimage": 2, "update_postimage": 3}


def load(name: str) -> list[dict]:
    with open(HERE / "data" / f"{name}.csv", newline="") as f:
        return [{**r, "price_cents": int(r["price_cents"])} for r in csv.DictReader(f)]


def statements(schema: str = SCHEMA) -> dict[str, str]:
    """{name: sql} from shelf_changes.sql, comments removed."""
    out = {}
    for chunk in (HERE / "shelf_changes.sql").read_text().split(";"):
        lines = chunk.strip().splitlines()
        if not lines:
            continue
        name = lines[0].split(":", 1)[1].strip()
        out[name] = "\n".join(l for l in lines if not l.lstrip().startswith("--")).replace("{schema}", schema)
    return out


def values_sql(name: str, rows: list[dict], schema: str = SCHEMA) -> str:
    values = ",\n".join(f"  ('{r['store']}', '{r['barcode']}', '{r['product']}', {r['price_cents']})" for r in rows)
    return (f"CREATE OR REPLACE TABLE {schema}.{name} AS\n"
            f"SELECT CAST(store AS STRING) AS store, CAST(barcode AS STRING) AS barcode,\n"
            f"       CAST(product AS STRING) AS product, CAST(price_cents AS INT) AS price_cents FROM VALUES\n"
            f"{values}\nAS t({', '.join(COLS)})")


def as_rows(raw) -> list[dict]:
    return [dict(zip(COLS, (r[0], r[1], r[2], int(r[3])))) for r in raw]


def sort_changes(changes: list[dict]) -> list[dict]:
    return sorted(changes, key=lambda c: (c["step"], c["store"], c["barcode"], TYPE_ORDER.get(c["type"], 9)))


def diff(before: list[dict], after: list[dict], step: int) -> list[dict]:
    """Row-level changes between two snapshots, keyed on (store, barcode), in change data feed shape."""
    b = {(r["store"], r["barcode"]): r for r in before}
    a = {(r["store"], r["barcode"]): r for r in after}
    out = []
    for k in sorted(b.keys() | a.keys()):
        if k not in a:
            out.append({"step": step, "type": "delete", **b[k]})
        elif k not in b:
            out.append({"step": step, "type": "insert", **a[k]})
        elif a[k] != b[k]:
            out += [{"step": step, "type": "update_preimage", **b[k]},
                    {"step": step, "type": "update_postimage", **a[k]}]
    return sort_changes(out)


# ---------- local: DuckDB runs the same UPDATE / DELETE / MERGE; a snapshot after each step gives the changes
def run_local(base: list[dict], feed: list[dict]) -> dict:
    import duckdb
    con = duckdb.connect()
    sql = statements(schema="main")
    con.execute("CREATE TABLE shelf_prices (store VARCHAR, barcode VARCHAR, product VARCHAR, price_cents INTEGER)")
    for name, rows in (("price_base", base), ("price_feed", feed)):
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM shelf_prices LIMIT 0")
        con.executemany(f"INSERT INTO {name} VALUES (?, ?, ?, ?)", [[r[c] for c in COLS] for r in rows])
    snap, changes = [], []
    for i, step in enumerate(STEPS, start=1):
        con.execute(sql[step])
        now = as_rows(con.sql(sql["snapshot"]).fetchall())
        changes += diff(snap, now, i)
        snap = now
    return {"changes": changes, "final": snap, "cdf_property": None}


# ---------- Databricks
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
        print("::error title=Databricks day 7::No SQL warehouse in the workspace")
        raise SystemExit(1)
    return next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])["id"]


def statement(wh: str, sql: str, name: str, fail: bool = True) -> list[list]:
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")[:500].replace("\n", " ")
        print(f"::{'error' if fail else 'warning'} title=Databricks day 7 {name} {res['status']['state']}::{msg}")
        if fail:
            raise SystemExit(1)
        return []
    return res.get("result", {}).get("data_array", []) or []


def cloud_changes(raw: list[list]) -> list[dict]:
    """table_changes rows -> local shape; commit versions become step numbers 1, 2, ... in commit order."""
    versions = sorted({int(r[0]) for r in raw})
    step = {v: i for i, v in enumerate(versions, start=1)}
    return sort_changes([{"step": step[int(r[0])], "type": r[1],
                          **dict(zip(COLS, (r[2], r[3], r[4], int(r[5]))))} for r in raw])


def run_databricks(base: list[dict], feed: list[dict]) -> dict:
    wh = _warehouse()
    sql = statements()
    statement(wh, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}", "create schema")
    try:
        statement(wh, f"DROP TABLE IF EXISTS {SCHEMA}.shelf_prices", "drop old shelf_prices")
        statement(wh, values_sql("price_base", base), "create price_base")
        statement(wh, values_sql("price_feed", feed), "create price_feed")
        statement(wh, sql["create"], "create shelf_prices")
        for step in STEPS:
            statement(wh, sql[step], step)
        props = {r[0]: r[1] for r in statement(wh, f"SHOW TBLPROPERTIES {SCHEMA}.shelf_prices", "properties")}
        return {"changes": cloud_changes(statement(wh, sql["changes"], "table_changes")),
                "final": as_rows(statement(wh, sql["snapshot"], "snapshot")),
                "cdf_property": props.get("delta.enableChangeDataFeed"),
                "row_tracking": props.get("delta.enableRowTracking")}
    finally:  # leave nothing behind
        for t in ("shelf_prices", "price_base", "price_feed"):
            statement(wh, f"DROP TABLE IF EXISTS {SCHEMA}.{t}", f"drop {t}", fail=False)
        statement(wh, f"DROP SCHEMA IF EXISTS {SCHEMA}", "drop schema", fail=False)


# ---------- comparison and summary
def net_effect(changes: list[dict]) -> dict:
    """{(step, store, barcode): [price before, price after]} with None for 'not on the shelf'."""
    out = {}
    for c in changes:
        k = (c["step"], c["store"], c["barcode"])
        before, after = out.get(k, [None, None])
        if c["type"] in ("delete", "update_preimage"):
            before = c["price_cents"]
        else:
            after = c["price_cents"]
        out[k] = [before, after]
    return out


def compare(cloud: dict, local: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if str(cloud.get("cdf_property") or "").lower() == "true":
        errors.append("delta.enableChangeDataFeed is set: this is legacy CDF, not automatic CDF")
    lnet, cnet = net_effect(local["changes"]), net_effect(cloud["changes"])
    bad = [f"step {k[0]} {k[1]}/{k[2]}: cloud {cnet.get(k)} local {lnet.get(k)}"
           for k in sorted(lnet.keys() | cnet.keys(), key=str) if lnet.get(k) != cnet.get(k)]
    if bad:
        errors.append(f"net change differs: {bad[:6]}")
    if cloud["final"] != local["final"]:
        errors.append(f"final table differs: {len(cloud['final'])} vs {len(local['final'])} rows")
    if not bad and cloud["changes"] != local["changes"]:
        warnings.append(f"change rows differ (same net effect): cloud {dict(Counter(c['type'] for c in cloud['changes']))}"
                        f" local {dict(Counter(c['type'] for c in local['changes']))}")
    return errors, warnings


def summarise(res: dict, feed_rows: int = 4) -> dict:
    changes = res["changes"]
    per_step = {s: dict(Counter(c["type"] for c in changes if c["step"] == i))
                for i, s in enumerate(STEPS, start=1)}
    # Shelf tags printed after step 1: what changed since? First price before -> last price after, per item.
    since = {}
    for (step, store, barcode), (before, after) in sorted(net_effect(changes).items()):
        if step >= 2:
            first = since.get((store, barcode), [before, None])[0]
            since[(store, barcode)] = [first, after]
    product = {(c["store"], c["barcode"]): c["product"] for c in changes}
    reprint = [{"store": s, "product": product[(s, b)], "was_cents": w, "now_cents": n,
                "action": "new tag" if w is None else "remove tag" if n is None else "reprint"}
               for (s, b), (w, n) in sorted(since.items())]
    return {"change_rows": len(changes), "per_step": per_step, "feed_rows": feed_rows,
            "feed_rows_unchanged": feed_rows - sum(1 for c in changes if c["step"] == 4
                                                   and c["type"] in ("insert", "update_postimage")),
            "final_items": len(res["final"]), "tags_to_change": reprint,
            "headline": f"{sum(r['action'] == 'reprint' for r in reprint)} reprints, "
                        f"{sum(r['action'] == 'new tag' for r in reprint)} new, "
                        f"{sum(r['action'] == 'remove tag' for r in reprint)} removed since the tags were printed"}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    base, feed = load("shelf_prices"), load("price_feed")
    local = run_local(base, feed)
    if mode == "--databricks":
        cloud = run_databricks(base, feed)
        errors, warnings = compare(cloud, local)
        for w in warnings:
            print(f"::warning title=Automatic CDF vs local::{w[:900]}")
        if errors:
            print(f"::error title=Databricks result differs from local::{json.dumps(errors)[:900]}")
            raise SystemExit(1)
        print("::notice title=Databricks day 7::table_changes() read automatic change data feed (row tracking "
              f"{cloud.get('row_tracking')}, enableChangeDataFeed {cloud.get('cdf_property')}) on a serverless SQL "
              f"warehouse and matched local: {json.dumps(summarise(cloud, len(feed)))[:700]}")
    else:
        summary = summarise(local, len(feed))
        (HERE / "result.json").write_text(json.dumps({"engine": "duckdb snapshot diff (local) / Databricks SQL "
                                                      "table_changes() on automatic change data feed (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
