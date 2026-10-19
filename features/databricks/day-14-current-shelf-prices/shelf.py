"""Day 14 (Databricks): current shelf prices and aisle top sellers with QUALIFY. Shelf-tag price changes arrive as an
event log (store, sku, effective_date, price); the price a shopper sees today is the newest row per store and SKU.
QUALIFY, new in Databricks SQL 2026.20, filters on a window function in the same SELECT, so "latest row per key" and
"top 2 per aisle" are one query each with no subquery.

  python shelf.py --make-data    # regenerate data/shelf_tags.csv and data/sales.csv (deterministic)
  python shelf.py --local        # plain Python does the same; writes result.json
  python shelf.py --sql          # print the Databricks statement
  python shelf.py --databricks   # serverless SQL warehouse via the Statement Execution API: run the query and compare
                                 # with local (nothing is created)

The cloud check:
  * errors: a missing or extra (store, sku) price row or aisle top-seller row, or any value that differs from local
Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed about
the workspace except the query results. Stores, prices and sales are made up.
"""
from __future__ import annotations

import csv
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAGS_CSV = HERE / "data" / "shelf_tags.csv"
SALES_CSV = HERE / "data" / "sales.csv"
STORES = ("S1", "S2", "S3")
# sku, aisle, base price (cents)
SKUS = [
    ("P01", "dairy", 389), ("P02", "dairy", 549), ("P03", "dairy", 429),
    ("P04", "produce", 69), ("P05", "produce", 299), ("P06", "produce", 449),
    ("P07", "pantry", 329), ("P08", "pantry", 199), ("P09", "pantry", 479),
    ("P10", "bakery", 499), ("P11", "bakery", 359),
]
TOP_N = 2


def make_data() -> tuple[list[dict], list[dict]]:
    rng = random.Random(14)
    tags, sales = [], []
    start = date(2026, 8, 3)
    for store in STORES:
        for sku, _aisle, base in SKUS:
            days = sorted(rng.sample(range(0, 76), rng.randint(1, 4)))
            price = base + rng.choice((-20, 0, 10, 20))
            for i, d in enumerate(days):
                if i:
                    price = max(49, price + rng.choice((-50, -30, -10, 10, 20, 40)))
                tags.append({"store": store, "sku": sku, "effective_date": (start + timedelta(d)).isoformat(),
                             "price_cents": str(price)})
    # sales: units per store and sku over the same weeks; P05 and P06 tie for 2nd in produce on purpose
    units = {"P01": 41, "P02": 18, "P03": 26, "P04": 77, "P05": 30, "P06": 30, "P07": 12, "P08": 35, "P09": 21,
             "P10": 14, "P11": 22}
    for sku, aisle, _ in SKUS:
        left = units[sku] * 3
        for i, store in enumerate(STORES):
            q = left if i == len(STORES) - 1 else rng.randint(units[sku] // 2, units[sku])
            left -= q
            sales.append({"store": store, "aisle": aisle, "sku": sku, "qty": str(q)})
    return tags, sales


def _read(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load() -> tuple[list[dict], list[dict]]:
    return _read(TAGS_CSV), _read(SALES_CSV)


def sql_statements(tags: list[dict], sales: list[dict]) -> dict[str, str]:
    tv = ",\n".join(f"    ('{t['store']}', '{t['sku']}', '{t['effective_date']}', {int(t['price_cents'])})" for t in tags)
    sv = ",\n".join(f"    ('{s['store']}', '{s['aisle']}', '{s['sku']}', {int(s['qty'])})" for s in sales)
    out = {}
    for chunk in (HERE / "shelf.sql").read_text().split(";"):
        lines_ = chunk.strip().splitlines()
        if not lines_:
            continue
        name = lines_[0].split(":", 1)[1].strip()
        body = "\n".join(l for l in lines_ if not l.lstrip().startswith("--"))
        out[name] = body.replace("{tags}", tv).replace("{sales}", sv)
    return out


# ---------- local: the same two answers in plain Python
def run_local(tags: list[dict], sales: list[dict]) -> list[dict]:
    rows = []
    hist: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for t in tags:
        hist.setdefault((t["store"], t["sku"]), []).append((t["effective_date"], int(t["price_cents"])))
    for (store, sku), h in hist.items():
        h.sort()
        rows.append({"kind": "price", "grp": store, "sku": sku, "value": h[-1][1], "detail": h[-1][0], "n": len(h),
                     "prev": h[-2][1] if len(h) > 1 else None})
    totals: dict[tuple[str, str], int] = {}
    stores: dict[tuple[str, str], set] = {}
    for s in sales:
        k = (s["aisle"], s["sku"])
        totals[k] = totals.get(k, 0) + int(s["qty"])
        stores.setdefault(k, set()).add(s["store"])
    for (aisle, sku), total in totals.items():
        rank = 1 + sum(1 for (a, _), v in totals.items() if a == aisle and v > total)  # RANK(): ties share a rank
        if rank <= TOP_N:
            rows.append({"kind": "top_seller", "grp": aisle, "sku": sku, "value": total, "detail": str(rank),
                         "n": len(stores[(aisle, sku)]), "prev": None})
    return sorted(rows, key=lambda r: (r["kind"], r["grp"], r["sku"]))


def rows_to_result(rows: list[list]) -> list[dict]:
    # Statement Execution API (JSON_ARRAY): every value is a string or null
    return [{"kind": r[0], "grp": r[1], "sku": r[2], "value": int(r[3]), "detail": r[4], "n": int(r[5]),
             "prev": None if r[6] is None else int(r[6])} for r in rows]


# ---------- Databricks SQL
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
        print("::error title=Databricks day 14::No SQL warehouse in the workspace")
        raise SystemExit(1)
    return next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])["id"]


def statement(wh: str, sql: str) -> list[list]:
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")[:500].replace("\n", " ")
        print(f"::error title=Databricks day 14 {res['status']['state']}::{msg}")
        raise SystemExit(1)
    return res.get("result", {}).get("data_array", []) or []


def run_databricks(tags: list[dict], sales: list[dict]) -> list[dict]:
    return rows_to_result(statement(_warehouse(), sql_statements(tags, sales)["databricks"]))


# ---------- comparison and summary
def compare(cloud: list[dict], local: list[dict]) -> list[str]:
    key = lambda r: (r["kind"], r["grp"], r["sku"])  # noqa: E731
    c, l = {key(r): r for r in cloud}, {key(r): r for r in local}
    errors = [f"{'/'.join(k)}: local only" for k in l if k not in c]
    errors += [f"{'/'.join(k)}: cloud only" for k in c if k not in l]
    if len(cloud) != len(c):
        errors.append(f"cloud returned {len(cloud) - len(c)} duplicate row(s)")
    for k, v in l.items():
        if k in c and c[k] != v:
            diff = {f: [c[k][f], v[f]] for f in v if c[k][f] != v[f]}
            errors.append(f"{'/'.join(k)}: cloud vs local {json.dumps(diff)}")
    return errors


def summarise(rows: list[dict], tags: list[dict]) -> dict:
    prices = [r for r in rows if r["kind"] == "price"]
    tops = [r for r in rows if r["kind"] == "top_seller"]
    changed = [r for r in prices if r["prev"] is not None]
    cuts = [r for r in changed if r["value"] < r["prev"]]
    spread = {}
    for sku in sorted({r["sku"] for r in prices}):
        p = [r["value"] for r in prices if r["sku"] == sku]
        spread[sku] = max(p) - min(p)
    widest = max(spread.items(), key=lambda kv: (kv[1], kv[0]))
    by_aisle: dict[str, list] = {}
    for r in tops:
        by_aisle.setdefault(r["grp"], []).append([r["sku"], r["value"], int(r["detail"])])
    return {
        "shelf_tag_rows": len(tags), "store_skus": len(prices),
        "rows_dropped_by_qualify": len(tags) - len(prices),
        "store_skus_with_changes": len(changed), "latest_change_is_cut": len(cuts),
        "widest_store_spread": {"sku": widest[0], "cents": widest[1]},
        "top_sellers": {a: sorted(v, key=lambda x: (x[2], x[0])) for a, v in sorted(by_aisle.items())},
        "headline": f"{len(tags)} shelf-tag rows -> {len(prices)} current prices; {len(cuts)} of {len(changed)} "
                    f"latest changes were cuts; {widest[0]} differs by {widest[1]} cents across stores; "
                    f"{len(tops)} top sellers in {len(by_aisle)} aisles (a tie kept by RANK)",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        for path, rows in zip((TAGS_CSV, SALES_CSV), make_data()):
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        raise SystemExit(0)
    tags, sales = load()
    if mode == "--sql":
        print(sql_statements(tags, sales)["databricks"] + ";")
    elif mode == "--databricks":
        local = run_local(tags, sales)
        cloud = run_databricks(tags, sales)
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{len(errors)} difference(s): "
                  f"{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud, tags)
        print("::notice title=Databricks day 14::QUALIFY ran on a serverless SQL warehouse and matched local for all "
              f"{s['store_skus']} store/SKU prices and {len(cloud) - s['store_skus']} top-seller rows: {s['headline']}; "
              f"top sellers {json.dumps(s['top_sellers'])}")
    else:
        summary = summarise(run_local(tags, sales), tags)
        (HERE / "result.json").write_text(json.dumps({"engine": "Python (local) / Databricks SQL QUALIFY (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
