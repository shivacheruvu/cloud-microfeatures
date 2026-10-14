"""Day 9 (Google Cloud): how many units a week will a new grocery item sell? Zero-shot regression with TabFM through
BigQuery's AI.PREDICT (Preview): no model is trained or stored; the 48 existing items are the context.

  python forecast.py --make-data      # writes data/items.csv (deterministic; already committed)
  python forecast.py --local          # least-squares on log(units) (pure Python) + naive mean; writes result.json
  python forecast.py --sql            # prints the BigQuery query (data inlined, nothing to load or clean up)
  python forecast.py --compare FILE   # checks BigQuery's JSON output against the local result

Data: weekly units = category base x price^-1.2 x shelf factor x promo lift x organic factor x noise, for 48 known
items (the context) and 8 new items whose first-week sales are known (held out, never sent as labels).
TabFM's answers aren't deterministic, so the cloud check is on properties:
  * errors:   a new item missing or without a positive finite prediction; TabFM's mean absolute error is not better
              than the naive "average of the known items" forecast
  * warnings: TabFM's MAE is more than 1.5x the local least-squares model's
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "items.csv"
FEATURES = ["category", "price", "shelf_level", "on_promo", "organic"]
LABEL = "weekly_units"
CATEGORY_BASE = {"dairy": 260, "produce": 340, "snacks": 180, "beverages": 220, "bakery": 150}
SHELF = {1: 0.75, 2: 1.0, 3: 1.25, 4: 0.9}   # 1 = bottom shelf, 3 = eye level
NEW_ITEMS = 8
WARN_RATIO = 1.5


def make_data() -> list[dict]:
    rng = random.Random(9)
    rows = []
    for i in range(48 + NEW_ITEMS):
        cat = list(CATEGORY_BASE)[i % 5]
        price = round(rng.uniform(1.5, 9.0), 2)
        shelf = rng.choice([1, 2, 3, 4])
        promo = rng.random() < 0.3
        organic = rng.random() < 0.35
        mean = (CATEGORY_BASE[cat] * (price / 4) ** -1.2 * SHELF[shelf] * (1.4 if promo else 1.0)
                * (0.85 if organic else 1.0))
        rows.append({"sku": f"{'N' if i >= 48 else 'K'}{i:03d}", "role": "new" if i >= 48 else "known",
                     "category": cat, "price": price, "shelf_level": shelf, "on_promo": promo, "organic": organic,
                     LABEL: round(mean * rng.uniform(0.9, 1.1), 1)})
    return rows


def load_rows() -> list[dict]:
    with open(DATA, newline="") as f:
        return [{**r, "price": float(r["price"]), "shelf_level": int(r["shelf_level"]),
                 "on_promo": r["on_promo"] == "True", "organic": r["organic"] == "True", LABEL: float(r[LABEL])}
                for r in csv.DictReader(f)]


def split(rows):
    return [r for r in rows if r["role"] == "known"], [r for r in rows if r["role"] == "new"]


# ---------- local: least squares on log(units) with one-hot category, log(price), shelf dummies, promo, organic
def design(r: dict) -> list[float]:
    cats = list(CATEGORY_BASE)[1:]
    return ([1.0] + [1.0 if r["category"] == c else 0.0 for c in cats] + [math.log(r["price"])]
            + [1.0 if r["shelf_level"] == s else 0.0 for s in (2, 3, 4)]
            + [1.0 if r["on_promo"] else 0.0, 1.0 if r["organic"] else 0.0])


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit(known: list[dict]) -> list[float]:
    x = [design(r) for r in known]
    y = [math.log(r[LABEL]) for r in known]
    k = len(x[0])
    xtx = [[sum(row[i] * row[j] for row in x) for j in range(k)] for i in range(k)]
    xty = [sum(row[i] * yy for row, yy in zip(x, y)) for i in range(k)]
    return solve(xtx, xty)


def mae(pred: dict[str, float], new: list[dict]) -> float:
    return sum(abs(pred[r["sku"]] - r[LABEL]) for r in new) / len(new)


def run_local(rows: list[dict]) -> dict:
    known, new = split(rows)
    beta = fit(known)
    ols = {r["sku"]: math.exp(sum(b * x for b, x in zip(beta, design(r)))) for r in new}
    naive_value = sum(r[LABEL] for r in known) / len(known)
    naive = {r["sku"]: naive_value for r in new}
    return {"ols": ols, "naive": naive, "mae_ols": round(mae(ols, new), 1), "mae_naive": round(mae(naive, new), 1)}


# ---------- BigQuery
def _struct(r: dict, with_label: bool, with_sku: bool) -> str:
    cols = []
    if with_sku:
        cols.append(f"'{r['sku']}' AS sku")
    cols += [f"'{r['category']}' AS category", f"{r['price']} AS price", f"{r['shelf_level']} AS shelf_level",
             f"{str(r['on_promo']).upper()} AS on_promo", f"{str(r['organic']).upper()} AS organic"]
    if with_label:
        cols.append(f"{r[LABEL]} AS {LABEL}")
    return "STRUCT(" + ", ".join(cols) + ")"


def bigquery_sql(rows: list[dict]) -> str:
    known, new = split(rows)
    return ((HERE / "forecast.sql").read_text()
            .replace("{known}", ",\n      ".join(_struct(r, True, False) for r in known))
            .replace("{new}", ",\n      ".join(_struct(r, False, True) for r in new)))


def read_bq(path: str) -> list[dict]:
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a warning line before the JSON
    try:
        return json.loads(text[start:]) if start >= 0 else []
    except Exception as e:  # noqa: BLE001 - any shape surprise is reported, redacted, as an annotation
        raw = text[max(start, 0):max(start, 0) + 500].replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {e}; {raw!r}")
        raise SystemExit(1)


def cloud_predictions(cloud: list[dict], new: list[dict]) -> dict[str, float | None]:
    """Map each output row to its new item: by sku when AI.PREDICT passes it through, else by the feature values."""
    by_features = {tuple(str(r[f]).lower() for f in FEATURES): r["sku"] for r in new}
    out = {}
    for c in cloud:
        sku = c.get("sku") or by_features.get(tuple(str(c.get(f)).lower() for f in FEATURES))
        v = c.get(f"predicted_{LABEL}")
        try:
            v = float(v) if v is not None else None
        except (TypeError, ValueError):
            v = None
        if sku:
            out[sku] = v
    return out


def compare(cloud: list[dict], rows: list[dict]) -> tuple[list[str], list[str], dict]:
    known, new = split(rows)
    local = run_local(rows)
    pred = cloud_predictions(cloud, new)
    errors, warnings = [], []
    for r in new:
        v = pred.get(r["sku"])
        if v is None or not math.isfinite(v) or v <= 0:
            errors.append(f"{r['sku']}: no positive prediction ({v})")
    if errors:
        return errors, warnings, {}
    m = round(mae(pred, new), 1)
    if m >= local["mae_naive"]:
        errors.append(f"TabFM MAE {m} is not better than the naive average's {local['mae_naive']}")
    if m > WARN_RATIO * local["mae_ols"]:
        warnings.append(f"TabFM MAE {m} vs local least-squares {local['mae_ols']}")
    return errors, warnings, {"mae_tabfm": m, "predictions": {k: round(v, 1) for k, v in sorted(pred.items())}}


def summarise(rows: list[dict], local: dict) -> dict:
    known, new = split(rows)
    return {
        "known_items": len(known), "new_items": len(new),
        "mae_naive": local["mae_naive"], "mae_least_squares": local["mae_ols"],
        "table": [{"sku": r["sku"], "category": r["category"], "price": r["price"], "shelf_level": r["shelf_level"],
                   "on_promo": r["on_promo"], "organic": r["organic"], "actual": r[LABEL],
                   "least_squares": round(local["ols"][r["sku"]], 1), "naive": round(local["naive"][r["sku"]], 1)}
                  for r in new],
        "headline": f"8 new items: least-squares MAE {local['mae_ols']} units/week vs naive {local['mae_naive']}; "
                    "TabFM zero-shot must beat naive",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        with open(DATA, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["sku", "role", *FEATURES, LABEL])
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
            print(f"::error title=AI.PREDICT output not understood::{type(e).__name__}: {e}; "
                  f"{len(cloud)} row(s), first: {json.dumps(cloud[:2])[:700]}")
            raise SystemExit(1)
        for w in warnings:
            print(f"::warning title=AI.PREDICT vs local::{w[:900]}")
        if errors:
            print(f"::error title=AI.PREDICT check failed::{json.dumps(errors)[:900]}; "
                  f"first row keys {sorted(cloud[0].keys()) if cloud else []}")
            raise SystemExit(1)
        local = run_local(rows)
        print(f"::notice title=Google Cloud day 9::AI.PREDICT (TabFM, zero-shot) ran on BigQuery: MAE "
              f"{summary['mae_tabfm']} units/week vs naive {local['mae_naive']} and local least-squares "
              f"{local['mae_ols']}; predictions {json.dumps(summary['predictions'])[:600]}")
    else:
        local = run_local(rows)
        summary = summarise(rows, local)
        (HERE / "result.json").write_text(json.dumps({"engine": "pure-Python least squares on log(units) (local) / "
                                                      "BigQuery AI.PREDICT with TabFM, zero-shot (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
