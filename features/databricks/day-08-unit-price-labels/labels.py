"""Day 8 (Databricks): unit-price shelf labels that never overstate, and "void last N scans", with the new SQL
functions `truncate` and `trim_array` (Databricks SQL 2026.38).

  python labels.py --local        # pure Python: exact fractions, truncated toward zero / rounded half-up, and list
                                  # slicing for the voids; writes result.json
  python labels.py --sql          # print the two Databricks statements
  python labels.py --databricks   # serverless SQL warehouse via the Statement Execution API: run both queries,
                                  # compare with local (nothing is created)

The cloud check:
  * errors:   any label (truncated unit price or truncated saving) or any basket (items kept, total) differs from
              local; the rounded comparison column differs from local
  * warnings: none expected
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
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
CENT = Decimal("0.01")


def load(name: str) -> list[dict]:
    with open(HERE / "data" / f"{name}.csv", newline="") as f:
        return list(csv.DictReader(f))


def products() -> list[dict]:
    return [{**r, "price_cents": int(r["price_cents"]), "promo_cents": int(r["promo_cents"]), "size": int(r["size"])}
            for r in load("products")]


def baskets() -> list[dict]:
    return [{"basket": r["basket"], "scans": r["scans"].split(), "voids": int(r["voids"])} for r in load("baskets")]


def to_decimal(x: Fraction, places: int = 12) -> Decimal:
    return (Decimal(x.numerator) / Decimal(x.denominator)).quantize(Decimal(1).scaleb(-places))


def trunc2(x: Fraction) -> Decimal:
    """Toward zero, like `truncate(x, 2)`: exact, so no intermediate rounding can push it over a cent."""
    q = abs(x) * 100
    cents = q.numerator // q.denominator
    return (Decimal(cents if x >= 0 else -cents) / 100).quantize(CENT)


def round2(x: Fraction) -> Decimal:
    return to_decimal(x, 20).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------- local
def run_local(prods: list[dict], bask: list[dict]) -> dict:
    labels = []
    for p in prods:
        unit = Fraction(p["price_cents"], p["size"])            # cents per g = dollars per 100 g
        saving = Fraction(p["promo_cents"] - p["price_cents"], p["size"])
        labels.append({"barcode": p["barcode"], "product": p["product"], "per": f"100 {p['unit']}",
                       "unit_price": to_decimal(unit, 6), "unit_label": trunc2(unit), "unit_rounded": round2(unit),
                       "saving_label": trunc2(saving)})
    price = {p["barcode"]: p["price_cents"] for p in prods}
    out = []
    for b in bask:
        kept = b["scans"][:len(b["scans"]) - b["voids"]]
        out.append({"basket": b["basket"], "scanned": len(b["scans"]), "voids": b["voids"], "kept": kept,
                    "items": len(kept), "total_cents": sum(price[x] for x in kept)})
    return {"labels": labels, "baskets": out}


# ---------- Databricks SQL
def sql_statements(prods: list[dict], bask: list[dict]) -> dict[str, str]:
    pv = ",\n".join(f"    ('{p['barcode']}', '{p['product']}', {p['price_cents']}, {p['promo_cents']}, {p['size']}, "
                    f"'{p['unit']}')" for p in prods)
    bv = ",\n".join(f"    ('{b['basket']}', array({', '.join(repr(s) for s in b['scans'])}), {b['voids']})"
                    for b in bask)
    out = {}
    for chunk in (HERE / "labels.sql").read_text().split(";"):
        lines = chunk.strip().splitlines()
        if not lines:
            continue
        name = lines[0].split(":", 1)[1].strip()
        body = "\n".join(l for l in lines if not l.lstrip().startswith("--"))
        out[name] = body.replace("{products}", pv).replace("{baskets}", bv)
    return out


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
        print("::error title=Databricks day 8::No SQL warehouse in the workspace")
        raise SystemExit(1)
    return next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])["id"]


def statement(wh: str, sql: str, name: str) -> list[list]:
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")[:500].replace("\n", " ")
        print(f"::error title=Databricks day 8 {name} {res['status']['state']}::{msg}")
        raise SystemExit(1)
    return res.get("result", {}).get("data_array", []) or []


def parse_cloud(label_rows: list[list], basket_rows: list[list]) -> dict:
    labels = [{"barcode": r[0], "product": r[1], "unit_price": Decimal(r[2]).quantize(Decimal("0.000001")),
               "unit_label": Decimal(r[3]).quantize(CENT), "unit_rounded": Decimal(r[4]).quantize(CENT),
               "saving_label": Decimal(r[5]).quantize(CENT)} for r in label_rows]
    bask = [{"basket": r[0], "kept": json.loads(r[1]), "items": int(r[2]), "total_cents": int(r[3])}
            for r in basket_rows]
    return {"labels": labels, "baskets": bask}


def run_databricks(prods: list[dict], bask: list[dict]) -> dict:
    wh = _warehouse()
    sql = sql_statements(prods, bask)
    return parse_cloud(statement(wh, sql["labels"], "labels"), statement(wh, sql["baskets"], "baskets"))


# ---------- comparison and summary
LABEL_KEYS = ("unit_label", "unit_rounded", "saving_label")
BASKET_KEYS = ("kept", "items", "total_cents")


def compare(cloud: dict, local: dict) -> list[str]:
    errors = []
    got = {r["barcode"]: r for r in cloud["labels"]}
    for loc in local["labels"]:
        c = got.get(loc["barcode"])
        if c is None:
            errors.append(f"{loc['product']}: no label row")
            continue
        bad = {k: (str(c[k]), str(loc[k])) for k in LABEL_KEYS if c[k] != loc[k]}
        if abs(c["unit_price"] - loc["unit_price"]) > Decimal("0.000001"):  # the raw quotient, before truncation
            bad["unit_price"] = (str(c["unit_price"]), str(loc["unit_price"]))
        if bad:
            errors.append(f"{loc['product']}: cloud/local {bad}")
    gotb = {r["basket"]: r for r in cloud["baskets"]}
    for loc in local["baskets"]:
        c = gotb.get(loc["basket"])
        if c is None or any(c[k] != loc[k] for k in BASKET_KEYS):
            errors.append(f"basket {loc['basket']}: cloud {c} local { {k: loc[k] for k in BASKET_KEYS} }")
    return errors


def summarise(res: dict) -> dict:
    labels = res["labels"]
    over = [l for l in labels if l["unit_rounded"] > l["unit_label"]]
    promo = [l for l in labels if l["saving_label"] != 0]
    return {
        "labels": len(labels),
        "rounding_would_overstate": len(over),
        "overstated_items": [l["product"] for l in over],
        "promo_labels": len(promo),
        "table": [{"product": l["product"], "per": l.get("per", ""), "unit_price": str(l["unit_price"]),
                   "label": str(l["unit_label"]), "rounded": str(l["unit_rounded"]),
                   "promo_saving": str(l["saving_label"])} for l in labels],
        "baskets": [{k: b[k] for k in ("basket", "scanned", "voids", "kept", "items", "total_cents") if k in b}
                    for b in res["baskets"]],
        "headline": f"rounding would overstate {len(over)} of {len(labels)} unit prices; truncate never does",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    prods, bask = products(), baskets()
    local = run_local(prods, bask)
    if mode == "--sql":
        for name, s in sql_statements(prods, bask).items():
            print(f"-- {name}\n{s};\n")
    elif mode == "--databricks":
        cloud = run_databricks(prods, bask)
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{json.dumps(errors)[:900]}")
            raise SystemExit(1)
        s = summarise(cloud)
        print("::notice title=Databricks day 8::truncate() and trim_array() ran on a serverless SQL warehouse and "
              f"matched local: {s['headline']}; labels "
              f"{json.dumps([[t['product'], t['label'], t['rounded'], t['promo_saving']] for t in s['table']])[:500]}; "
              f"baskets {json.dumps([[b['basket'], b['items'], b['total_cents']] for b in s['baskets']])}")
    else:
        summary = summarise(local)
        (HERE / "result.json").write_text(json.dumps({"engine": "pure-Python exact fractions (local) / Databricks "
                                                      "SQL truncate() and trim_array() (cloud)", **summary},
                                                     indent=2) + "\n")
        print(json.dumps(summary, indent=2))
