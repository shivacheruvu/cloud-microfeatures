"""Day 13 (Databricks): basket and aisle allergen summary. Every product carries an array of allergen tags (Open Food
Facts style); collect_union, the aggregate new in Databricks SQL 2026.35, merges those arrays into the distinct set of
allergens per shopping basket and per aisle in one GROUP BY.

  python allergens.py --make-data    # regenerate data/products.csv and data/basket_lines.csv (deterministic)
  python allergens.py --local        # Python set unions do the same summary; writes result.json
  python allergens.py --sql          # print the Databricks statement
  python allergens.py --databricks   # serverless SQL warehouse via the Statement Execution API: run the summary and
                                     # compare with local (nothing is created)

The cloud check:
  * errors: any basket or aisle whose sorted allergen set differs from the local set unions, or where collect_union
    disagrees with the long-hand array_distinct(flatten(collect_list(...))) computed in the same query
Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed
about the workspace except the query results. Products and baskets are made up.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRODUCTS_CSV = HERE / "data" / "products.csv"
LINES_CSV = HERE / "data" / "basket_lines.csv"
NUTS = ("peanuts", "tree_nuts")

# sku, name, aisle, allergen tags (EU/US major allergens as Open Food Facts-style tags; "" = none declared)
PRODUCTS = [
    ("P01", "Whole milk 1 gal", "dairy", "milk"),
    ("P02", "Greek yogurt", "dairy", "milk"),
    ("P03", "Oat milk", "dairy", "gluten"),
    ("P04", "Cheddar block", "dairy", "milk"),
    ("P05", "Sourdough loaf", "bakery", "gluten"),
    ("P06", "Almond croissant", "bakery", "gluten|milk|eggs|tree_nuts"),
    ("P07", "Gluten-free bagels", "bakery", "eggs|sesame"),
    ("P08", "Bananas", "produce", ""),
    ("P09", "Baby spinach", "produce", ""),
    ("P10", "Avocados", "produce", ""),
    ("P11", "Peanut butter", "pantry", "peanuts"),
    ("P12", "Soy sauce", "pantry", "soy|gluten"),
    ("P13", "Tahini", "pantry", "sesame"),
    ("P14", "Trail mix", "pantry", "peanuts|tree_nuts|soy"),
    ("P15", "Canned tuna", "pantry", "fish"),
    ("P16", "Frozen shrimp", "frozen", "crustaceans"),
    ("P17", "Fish sticks", "frozen", "fish|gluten|milk|eggs"),
    ("P18", "Frozen peas", "frozen", ""),
]
# basket_id, store, [(sku, qty)]
BASKETS = [
    ("B01", "S1", [("P08", 6), ("P09", 1), ("P10", 2)]),
    ("B02", "S1", [("P01", 1), ("P05", 1), ("P11", 1)]),
    ("B03", "S1", [("P06", 2), ("P02", 4)]),
    ("B04", "S1", [("P12", 1), ("P16", 1), ("P18", 2)]),
    ("B05", "S1", [("P03", 2), ("P07", 1), ("P13", 1)]),
    ("B06", "S1", [("P15", 3), ("P17", 1), ("P09", 1)]),
    ("B07", "S2", [("P08", 3), ("P18", 1)]),
    ("B08", "S2", [("P14", 2), ("P04", 1), ("P05", 1), ("P01", 2)]),
    ("B09", "S2", [("P02", 2), ("P04", 1), ("P01", 1)]),
    ("B10", "S2", [("P11", 1), ("P13", 1), ("P07", 2), ("P10", 1)]),
    ("B11", "S2", [("P16", 2), ("P15", 1), ("P12", 1), ("P06", 1)]),
    ("B12", "S2", [("P09", 1), ("P10", 1), ("P03", 1)]),
]


def make_data() -> tuple[list[dict], list[dict]]:
    products = [{"sku": s, "name": n, "aisle": a, "allergens": t} for s, n, a, t in PRODUCTS]
    lines = [{"basket_id": b, "store": st, "sku": sku, "qty": str(q)} for b, st, items in BASKETS for sku, q in items]
    return products, lines


def _read(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load() -> tuple[list[dict], list[dict]]:
    return _read(PRODUCTS_CSV), _read(LINES_CSV)


def tags(p: dict) -> list[str]:
    return [t for t in p["allergens"].split("|") if t]


def _array_literal(values: list[str]) -> str:
    if not values:
        return "CAST(array() AS ARRAY<STRING>)"
    return "array(" + ", ".join(f"'{v}'" for v in values) + ")"


def sql_statements(products: list[dict], lines: list[dict]) -> dict[str, str]:
    pv = ",\n".join(f"    ('{p['sku']}', '{p['aisle']}', {_array_literal(tags(p))})" for p in products)
    lv = ",\n".join(f"    ('{l['basket_id']}', '{l['store']}', '{l['sku']}', {int(l['qty'])})" for l in lines)
    out = {}
    for chunk in (HERE / "allergens.sql").read_text().split(";"):
        lines_ = chunk.strip().splitlines()
        if not lines_:
            continue
        name = lines_[0].split(":", 1)[1].strip()
        body = "\n".join(l for l in lines_ if not l.lstrip().startswith("--"))
        out[name] = body.replace("{products}", pv).replace("{lines}", lv)
    return out


# ---------- local: the same summary with Python sets
def run_local(products: list[dict], lines: list[dict]) -> list[dict]:
    by_sku = {p["sku"]: p for p in products}
    groups: dict[tuple[str, str], set[str]] = {}
    for p in products:
        groups.setdefault(("aisle", p["aisle"]), set()).update(tags(p))
    for l in lines:
        groups.setdefault(("basket", l["basket_id"]), set()).update(tags(by_sku[l["sku"]]))
    return sorted(({"level": lv, "key": k, "allergens": sorted(s), "n_allergens": len(s),
                    "nut_free": not any(n in s for n in NUTS), "matches_flatten": True}
                   for (lv, k), s in groups.items()), key=lambda r: (r["level"], r["key"]))


def rows_to_result(rows: list[list]) -> list[dict]:
    # Statement Execution API (JSON_ARRAY): every value is a string or null; arrays arrive as JSON text
    def arr(v):
        return [] if v is None else json.loads(v) if isinstance(v, str) else list(v)

    def boolean(v):
        return v if isinstance(v, bool) else str(v).lower() == "true"

    return [{"level": r[0], "key": r[1], "allergens": arr(r[2]), "n_allergens": int(r[3]),
             "nut_free": boolean(r[4]), "matches_flatten": boolean(r[5])} for r in rows]


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
        print("::error title=Databricks day 13::No SQL warehouse in the workspace")
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
        print(f"::error title=Databricks day 13 {res['status']['state']}::{msg}")
        raise SystemExit(1)
    return res.get("result", {}).get("data_array", []) or []


def run_databricks(products: list[dict], lines: list[dict]) -> list[dict]:
    return rows_to_result(statement(_warehouse(), sql_statements(products, lines)["databricks"]))


# ---------- comparison and summary
def compare(cloud: list[dict], local: list[dict]) -> list[str]:
    c = {(r["level"], r["key"]): r for r in cloud}
    l = {(r["level"], r["key"]): r for r in local}
    errors = [f"{'/'.join(k)}: local only" for k in l if k not in c]
    errors += [f"{'/'.join(k)}: cloud only" for k in c if k not in l]
    for k, v in l.items():
        if k in c and c[k] != v:
            diff = {f: [c[k][f], v[f]] for f in v if c[k][f] != v[f]}
            errors.append(f"{'/'.join(k)}: cloud vs local {json.dumps(diff)}")
    return errors


def summarise(rows: list[dict]) -> dict:
    baskets = [r for r in rows if r["level"] == "basket"]
    aisles = [r for r in rows if r["level"] == "aisle"]
    free = [r["key"] for r in baskets if r["n_allergens"] == 0]
    nut = [r["key"] for r in baskets if not r["nut_free"]]
    widest = max(baskets, key=lambda r: (r["n_allergens"], r["key"]))
    freq: dict[str, int] = {}
    for r in baskets:
        for a in r["allergens"]:
            freq[a] = freq.get(a, 0) + 1
    top = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "baskets": len(baskets), "aisles": len(aisles),
        "allergen_free_baskets": free, "nut_baskets": nut,
        "widest_basket": {"basket_id": widest["key"], "allergens": widest["allergens"]},
        "baskets_per_allergen": dict(top),
        "aisle_allergens": {r["key"]: r["allergens"] for r in aisles},
        "nut_free_aisles": [r["key"] for r in aisles if r["nut_free"]],
        "headline": f"{len(free)} of {len(baskets)} baskets allergen-free, {len(nut)} contain nuts; "
                    f"{top[0][0]} is in {top[0][1]} baskets; basket {widest['key']} has {widest['n_allergens']} "
                    f"allergens; {sum(r['nut_free'] for r in aisles)} of {len(aisles)} aisles nut-free",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        for path, rows in zip((PRODUCTS_CSV, LINES_CSV), make_data()):
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        raise SystemExit(0)
    products, lines = load()
    if mode == "--sql":
        print(sql_statements(products, lines)["databricks"] + ";")
    elif mode == "--databricks":
        local = run_local(products, lines)
        cloud = run_databricks(products, lines)
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{len(errors)} difference(s): "
                  f"{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud)
        print("::notice title=Databricks day 13::collect_union ran on a serverless SQL warehouse and matched the local "
              f"set unions (and array_distinct(flatten(collect_list))) for all {s['baskets']} baskets and "
              f"{s['aisles']} aisles: {s['headline']}; nut baskets {json.dumps(s['nut_baskets'])}")
    else:
        summary = summarise(run_local(products, lines))
        (HERE / "result.json").write_text(json.dumps({"engine": "Python set unions (local) / Databricks SQL "
                                                      "collect_union (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
