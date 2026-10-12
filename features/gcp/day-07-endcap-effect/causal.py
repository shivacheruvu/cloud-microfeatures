"""Day 7 (Google Cloud): did the 2026-09-01 shelf reset move sales? BigQuery's AI.CAUSAL_EFFECT (Preview), per product.

  python causal.py --make-data      # writes data/daily_units.csv (deterministic; already committed)
  python causal.py --local          # weekday-mean counterfactual + z-test (pure Python); writes result.json
  python causal.py --sql            # prints the BigQuery query (data inlined, nothing to load or clean up)
  python causal.py --compare FILE   # checks BigQuery's JSON output against the local result

On 2026-09-01 kombucha moved to an endcap, oat milk moved to the bottom shelf, and sparkling water stayed put.
Local method: the counterfactual for each post-reset day is the mean of the same weekday over the 12 weeks before;
the summed effect is tested against the spread of the pre-period's own day-to-day residuals (normal approximation).
An effect is "lift" or "drop" when p < 0.05, else "no effect". The cloud check:
  * errors:   any non-empty status; a product missing; BigQuery's class differs from local
  * warnings: relative effect off by more than max(5 points, 35%)
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "daily_units.csv"
START, INTERVENTION, POST_DAYS, PRE_DAYS = date(2026, 6, 9), date(2026, 9, 1), 28, 84
ALPHA = 0.05
REL_ABS_TOL, REL_REL_TOL = 0.05, 0.35
# product: (base units a day, effect after the reset as a share of base)
PRODUCTS = {"kombucha 16oz": (30, 0.40), "oat milk 64oz": (45, -0.30), "sparkling water 12pk": (38, 0.0)}
WEEKDAY = [0.85, 0.80, 0.85, 0.95, 1.10, 1.35, 1.10]  # Mon..Sun


def make_data() -> list[dict]:
    """Weekly pattern + small noise. Post-reset noise sums to zero each week, so the no-effect series stays null."""
    rng = random.Random(7)
    rows = []
    for product, (base, effect) in PRODUCTS.items():
        post_noise = [2, -2, 1, -1, 0, 3, -3]
        for i in range(PRE_DAYS + POST_DAYS):
            d = START + timedelta(days=i)
            post = d >= INTERVENTION
            if post and (i - PRE_DAYS) % 7 == 0:
                rng.shuffle(post_noise)
            noise = post_noise[(i - PRE_DAYS) % 7] if post else rng.randint(-3, 3)
            mean = base * WEEKDAY[d.weekday()] * (1 + effect if post else 1)
            rows.append({"sale_date": d.isoformat(), "product": product, "units": max(0, round(mean) + noise)})
    return rows


def load_rows() -> list[dict]:
    with open(DATA, newline="") as f:
        return [{**r, "units": int(r["units"])} for r in csv.DictReader(f)]


def series(rows: list[dict]) -> dict[str, list[tuple[date, int]]]:
    out = defaultdict(list)
    for r in rows:
        out[r["product"]].append((date.fromisoformat(r["sale_date"]), r["units"]))
    return {p: sorted(v) for p, v in sorted(out.items())}


def bigquery_sql(rows: list[dict]) -> str:
    structs = [f"    STRUCT(DATE '{r['sale_date']}' AS sale_date, '{r['product']}' AS product, {r['units']} AS units)"
               if i == 0 else f"    (DATE '{r['sale_date']}', '{r['product']}', {r['units']})"
               for i, r in enumerate(rows)]
    return ((HERE / "causal.sql").read_text().replace("{values}", ",\n".join(structs))
            .replace("{intervention}", INTERVENTION.isoformat()))


def classify(p_value: float, relative: float) -> str:
    return "no effect" if p_value >= ALPHA else "lift" if relative > 0 else "drop"


def local_effect(points: list[tuple[date, int]]) -> dict:
    pre = [(d, y) for d, y in points if d < INTERVENTION]
    post = [(d, y) for d, y in points if d >= INTERVENTION]
    by_wd = defaultdict(list)
    for d, y in pre:
        by_wd[d.weekday()].append(y)
    wd_mean = {wd: statistics.fmean(v) for wd, v in by_wd.items()}
    sigma = statistics.stdev(y - wd_mean[d.weekday()] for d, y in pre)
    expected = sum(wd_mean[d.weekday()] for d, _ in post)
    absolute = sum(y for _, y in post) - expected
    z = absolute / (sigma * math.sqrt(len(post)))
    p = math.erfc(abs(z) / math.sqrt(2))  # two-tailed
    relative = absolute / expected
    return {"class": classify(p, relative), "p_value": round(p, 4), "absolute_effect": round(absolute, 1),
            "relative_effect": round(relative, 3), "post_days": len(post), "expected_units": round(expected, 1)}


def run_local(rows: list[dict]) -> dict[str, dict]:
    return {p: local_effect(pts) for p, pts in series(rows).items()}


def read_bq(path: str) -> list[dict]:
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a warning line before the JSON
    try:
        rows = json.loads(text[start:]) if start >= 0 else []
        num = lambda v: None if v is None else float(v)  # noqa: E731
        return [{"product": r.get("product"), "status": r.get("status") or "", "p_value": num(r.get("p_value")),
                 "absolute_effect": num(r.get("absolute_effect")), "relative_effect": num(r.get("relative_effect"))}
                for r in rows]
    except Exception as e:  # noqa: BLE001 - any shape surprise is reported, redacted, as an annotation
        raw = text[max(start, 0):max(start, 0) + 500].replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {e}; {raw!r}")
        raise SystemExit(1)


def compare(cloud: list[dict], rows: list[dict]) -> tuple[list[str], list[str], dict]:
    local = run_local(rows)
    errors, warnings, summary = [], [], {}
    bad = [r for r in cloud if r["status"]]
    if bad:
        errors.append(f"status: {bad[0]['status'][:200]}")
    got = {r["product"]: r for r in cloud}
    for product, loc in local.items():
        r = got.get(product)
        if r is None or r["p_value"] is None or r["relative_effect"] is None:
            errors.append(f"{product}: no result row")
            continue
        cls = classify(r["p_value"], r["relative_effect"])
        summary[product] = {"class": cls, "p_value": round(r["p_value"], 4),
                            "relative_effect": round(r["relative_effect"], 3),
                            "absolute_effect": round(r["absolute_effect"] or 0, 1)}
        if cls != loc["class"]:
            errors.append(f"{product}: BigQuery says {cls} (p={r['p_value']:.4f}, {r['relative_effect']:+.3f}), "
                          f"local {loc['class']} (p={loc['p_value']}, {loc['relative_effect']:+.3f})")
        diff = abs(r["relative_effect"] - loc["relative_effect"])
        if diff > max(REL_ABS_TOL, REL_REL_TOL * abs(loc["relative_effect"])):
            warnings.append(f"{product}: relative effect bq={r['relative_effect']:+.3f} local={loc['relative_effect']:+.3f}")
    return errors, warnings, summary


def summarise(local: dict[str, dict], rows: list[dict]) -> dict:
    ser = series(rows)
    return {"intervention": INTERVENTION.isoformat(), "pre_days": PRE_DAYS, "post_days": POST_DAYS,
            "products": len(ser), "effects": local,
            "headline": "; ".join(f"{p.rsplit(' ', 1)[0]}: {v['class']} {v['relative_effect']:+.1%}" for p, v in local.items())}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        with open(DATA, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["sale_date", "product", "units"])
            w.writeheader()
            w.writerows(make_data())
        raise SystemExit(0)
    rows = load_rows()
    if mode == "--sql":
        print(bigquery_sql(rows))
    elif mode == "--compare":
        cloud = read_bq(sys.argv[2])
        try:
            errors, warnings, summary = compare(cloud, rows)
        except Exception as e:  # report what BigQuery returned (data rows only: no project or account details)
            print(f"::error title=AI.CAUSAL_EFFECT output not understood::{type(e).__name__}: {e}; "
                  f"{len(cloud)} row(s), first: {json.dumps(cloud[:2])[:700]}")
            raise SystemExit(1)
        for w in warnings:
            print(f"::warning title=AI.CAUSAL_EFFECT vs local::{w[:900]}")
        if errors:
            print(f"::error title=AI.CAUSAL_EFFECT differs from local::{json.dumps(errors)[:900]}")
            raise SystemExit(1)
        print(f"::notice title=Google Cloud day 7::AI.CAUSAL_EFFECT ran on BigQuery and agreed with local "
              f"({json.dumps(summarise(run_local(rows), rows)['headline'])}): {json.dumps(summary)[:700]}")
    else:
        summary = summarise(run_local(rows), rows)
        (HERE / "result.json").write_text(json.dumps({"engine": "pure-Python weekday-mean counterfactual + z-test "
                                                      "(local) / BigQuery AI.CAUSAL_EFFECT on ARIMA_PLUS (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
