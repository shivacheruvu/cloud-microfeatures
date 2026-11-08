"""Day 34 (Google Cloud) job: shelf prices and basket totals, written for Python 3.15.

Runs as a Cloud Run job on Google's Python 3.15 runtime base image (python315, Preview since 2026-11-02). This file
needs Python 3.15: it uses
  * PEP 798 unpacking in comprehensions: `{**layer for layer in layers}` and `[*b["lines"] for b in baskets]`
  * PEP 814 the built-in `frozendict`: promo rules from two feeds become hashable, so duplicates collapse in a set
    whatever their key order, and each rule can be a dict key
  * PEP 810 explicit lazy imports: `lazy import statistics` is only loaded when the median is computed
  * PEP 686 UTF-8 mode by default: the receipt file is written with no `encoding=` and must come back as UTF-8

It is self-contained (the cloud run sends it as base64 in DAY34_CODE and the fixture in DAY34_DATA) and prints one
`DAY34_RESULT {json}` line. `rollup.py` is the Python 3.12 reference it must match.

  python3.15 job315.py data.json      # local run
"""
import base64
import json
import os
import sys
import tempfile

lazy import statistics


def effective_prices(data):
    """Store -> {sku: cents}; later layers override earlier ones (chain, then region, then store)."""
    layers = data["price_layers"]
    return {store: {**layers[name] for name in stack} for store, stack in data["stores"].items()}


def distinct_rules(data):
    raw = [*feed for feed in data["promo_feeds"]]
    rules = {frozendict(r) for r in raw}
    by_sku = {r["sku"]: r for r in rules}
    return raw, rules, by_sku


def discount(rule, unit, qty):
    if rule is None or qty < rule["min_qty"]:
        return 0
    if rule["kind"] == "pct":
        return unit * qty * rule["value"] // 100
    return rule["value"] * qty


def compute(data):
    prices = effective_prices(data)
    raw, rules, by_sku = distinct_rules(data)
    hits = {r: 0 for r in rules}
    store_rev, basket_net = {}, []
    for b in data["baskets"]:
        gross = disc = 0
        for sku, qty in b["lines"]:
            unit = prices[b["store"]][sku]
            rule = by_sku.get(sku)
            d = discount(rule, unit, qty)
            if d:
                hits[rule] += 1
            gross, disc = gross + unit * qty, disc + d
        basket_net.append(gross - disc)
        s = store_rev.setdefault(b["store"], {"gross": 0, "discount": 0, "net": 0, "baskets": 0})
        s["gross"] += gross
        s["discount"] += disc
        s["net"] += gross - disc
        s["baskets"] += 1
    lines = [*b["lines"] for b in data["baskets"]]
    lazy_before = "statistics" in sys.modules
    median = statistics.median(basket_net)
    return {
        "stores": len(prices),
        "baskets": len(data["baskets"]),
        "lines": len(lines),
        "units": sum(q for _, q in lines),
        "promo_rules_raw": len(raw),
        "promo_rules_distinct": len(rules),
        "rule_hits": sorted([r["sku"], r["kind"], r["value"], n] for r, n in hits.items()),
        "shelf_prices": {s: dict(sorted(p.items())) for s, p in sorted(prices.items())},
        "store_totals": dict(sorted(store_rev.items())),
        "gross_cents": sum(s["gross"] for s in store_rev.values()),
        "discount_cents": sum(s["discount"] for s in store_rev.values()),
        "net_cents": sum(s["net"] for s in store_rev.values()),
        "median_basket_cents": median,
        "_lazy_import_deferred": not lazy_before,
    }


def receipt_check(data):
    """Write product names (Jalapeño, Crème fraîche, Café) with no encoding= and read the bytes back."""
    names = sorted(data["products"].values())
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "receipt.txt")
        with open(path, "w") as f:          # no encoding: UTF-8 by default in Python 3.15 (PEP 686)
            f.write("\n".join(names))
        raw = open(path, "rb").read()
    return {"bytes": len(raw), "utf8": raw.decode("utf-8") == "\n".join(names)}


def main(argv):
    if os.environ.get("DAY34_DATA"):
        data = json.loads(base64.b64decode(os.environ["DAY34_DATA"]))
    else:
        data = json.load(open(argv[1] if len(argv) > 1 else "data.json", encoding="utf-8"))
    res = compute(data)
    res["runtime"] = {
        "python": "%d.%d.%d" % sys.version_info[:3],
        "release_level": sys.version_info.releaselevel,
        "utf8_mode": sys.flags.utf8_mode,
        "lazy_import_deferred": res.pop("_lazy_import_deferred"),
        "receipt": receipt_check(data),
        "frozendict_builtin": isinstance(frozendict, type),
    }
    print("DAY34_RESULT " + json.dumps(res, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__" or os.environ.get("DAY34_MODE") == "job":
    sys.exit(main(sys.argv))
