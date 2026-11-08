"""Day 34 (Google Cloud): shelf prices and basket totals on a Cloud Run job running Google's **Python 3.15 runtime**
base image (python315, Preview, Cloud Run functions release notes 2026-11-02).

  python rollup.py            # Python 3.12+ reference (no 3.15 syntax), writes result.json
  python rollup.py --cloud    # GitHub Actions: run job315.py on the python315 runtime image, compare, delete the job

`job315.py` is the same computation written with Python 3.15 features (unpacking in comprehensions, the built-in
frozendict, lazy imports, UTF-8 mode by default) and can't even be parsed by Python 3.14 or older. The cloud result
must equal this reference, and the job must report Python 3.15 with UTF-8 mode on.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data.json"
JOB = "cmf-d34-py315-shelf"
RUNTIME_ID = "python315"
IMAGE = f"us-central1-docker.pkg.dev/serverless-runtimes/google-24-full/runtimes/{RUNTIME_ID}"
KEYS = ("stores", "baskets", "lines", "units", "promo_rules_raw", "promo_rules_distinct", "rule_hits", "shelf_prices",
        "store_totals", "gross_cents", "discount_cents", "net_cents", "median_basket_cents")


def load(path: Path = DATA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def effective_prices(data: dict) -> dict:
    out = {}
    for store, stack in data["stores"].items():
        merged: dict = {}
        for name in stack:
            merged.update(data["price_layers"][name])
        out[store] = merged
    return out


def distinct_rules(data: dict):
    raw = [r for feed in data["promo_feeds"] for r in feed]
    keyed = {tuple(sorted(r.items())): r for r in raw}       # what frozendict does for free in 3.15
    return raw, list(keyed.values())


def discount(rule: dict | None, unit: int, qty: int) -> int:
    if rule is None or qty < rule["min_qty"]:
        return 0
    if rule["kind"] == "pct":
        return unit * qty * rule["value"] // 100
    return rule["value"] * qty


def reference(data: dict | None = None) -> dict:
    data = data or load()
    prices = effective_prices(data)
    raw, rules = distinct_rules(data)
    by_sku = {r["sku"]: r for r in rules}
    hits = {r["sku"]: 0 for r in rules}
    store_rev: dict = {}
    basket_net = []
    for b in data["baskets"]:
        gross = disc = 0
        for sku, qty in b["lines"]:
            unit = prices[b["store"]][sku]
            d = discount(by_sku.get(sku), unit, qty)
            if d:
                hits[sku] += 1
            gross, disc = gross + unit * qty, disc + d
        basket_net.append(gross - disc)
        s = store_rev.setdefault(b["store"], {"gross": 0, "discount": 0, "net": 0, "baskets": 0})
        s["gross"] += gross
        s["discount"] += disc
        s["net"] += gross - disc
        s["baskets"] += 1
    lines = [ln for b in data["baskets"] for ln in b["lines"]]
    return {
        "stores": len(prices),
        "baskets": len(data["baskets"]),
        "lines": len(lines),
        "units": sum(q for _, q in lines),
        "promo_rules_raw": len(raw),
        "promo_rules_distinct": len(rules),
        "rule_hits": sorted([r["sku"], r["kind"], r["value"], hits[r["sku"]]] for r in rules),
        "shelf_prices": {s: dict(sorted(p.items())) for s, p in sorted(prices.items())},
        "store_totals": dict(sorted(store_rev.items())),
        "gross_cents": sum(s["gross"] for s in store_rev.values()),
        "discount_cents": sum(s["discount"] for s in store_rev.values()),
        "net_cents": sum(s["net"] for s in store_rev.values()),
        "median_basket_cents": statistics.median(basket_net),
    }


def write_result() -> dict:
    res = {"engine": "Python 3.12 reference (local) / job315.py on the Cloud Run python315 runtime image (cloud)",
           "image": IMAGE, **reference()}
    (HERE / "result.json").write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return res


if __name__ == "__main__":
    if "--cloud" in sys.argv:
        import cloud_run
        sys.exit(cloud_run.run())
    r = write_result()
    print(f"{r['baskets']} baskets, {r['lines']} lines at {r['stores']} stores: gross {r['gross_cents']}c, "
          f"discount {r['discount_cents']}c, net {r['net_cents']}c; promo rules {r['promo_rules_raw']} -> "
          f"{r['promo_rules_distinct']} distinct")
