"""Day 21 (Google Cloud): will this shelf run out before close? A BigQuery ML boosted-tree classifier trained with
XGBoost 2.1 (the XGBOOST_VERSION option, GA 2026-08-27) on store-shelf days.

  python stockout.py --local                 # generate the data, score the baselines, write result.json
  python stockout.py --csv OUT.csv           # the shelf-day table as CSV (for bq load)
  python stockout.py --sql PART DATASET      # print a SQL statement: model | predict
  python stockout.py --compare PRED.json     # check BigQuery's holdout predictions, print annotations

The data is synthetic and deterministic (seeded): each row is one product on one store day, with the opening
on-hand count, the demand forecast, promo / weekend flags and the department. The truth is whether actual demand
(forecast x promo lift x weekend lift x noise) exceeded on-hand. The naive rule "on_hand < forecast" ignores the
lifts; the model has to learn them.
"""
from __future__ import annotations

import csv
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 21
N_TRAIN, N_HOLDOUT = 600, 200
DEPARTMENTS = {"produce": 1.0, "bakery": 1.15, "dairy": 0.9, "frozen": 0.8}  # department-level demand bias
PROMO_LIFT, WEEKEND_LIFT = 1.6, 1.25
XGBOOST_VERSION = "2.1"
FEATURES = ["department", "is_promo", "is_weekend", "on_hand", "forecast_units", "cover_ratio"]
MIN_ACCURACY = 0.85   # BigQuery's holdout accuracy must be at least this ...
MIN_LIFT = 0.10       # ... and beat the naive rule by at least this much


def generate() -> list[dict]:
    rng = random.Random(SEED)
    rows = []
    for i in range(N_TRAIN + N_HOLDOUT):
        dept = rng.choice(sorted(DEPARTMENTS))
        promo = rng.random() < 0.25
        weekend = rng.random() < 2 / 7
        forecast = round(rng.uniform(8, 60), 1)
        on_hand = int(forecast * rng.uniform(0.6, 2.2))
        demand = forecast * DEPARTMENTS[dept] * (PROMO_LIFT if promo else 1) * (WEEKEND_LIFT if weekend else 1) \
            * rng.uniform(0.9, 1.1)
        rows.append({"shelf_day_id": i + 1, "split": "train" if i < N_TRAIN else "holdout", "department": dept,
                     "is_promo": promo, "is_weekend": weekend, "on_hand": on_hand, "forecast_units": forecast,
                     "cover_ratio": round(on_hand / forecast, 3), "stockout": demand > on_hand})
    return rows


def naive_rule(r: dict) -> bool:
    """What a shelf planner does today: flag a shelf when on-hand is below the forecast."""
    return r["on_hand"] < r["forecast_units"]


def lift_rule(r: dict) -> bool:
    """The rule with the known lifts but no noise: the best any model can roughly do."""
    exp = r["forecast_units"] * DEPARTMENTS[r["department"]] * (PROMO_LIFT if r["is_promo"] else 1) \
        * (WEEKEND_LIFT if r["is_weekend"] else 1)
    return exp > r["on_hand"]


def accuracy(rows: list[dict], pred) -> float:
    return round(sum(pred(r) == r["stockout"] for r in rows) / len(rows), 3)


def write_csv(rows: list[dict], path: str) -> None:
    keys = ["shelf_day_id", "split", *FEATURES, "stockout"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (str(r[k]).lower() if isinstance(r[k], bool) else r[k]) for k in keys})


SCHEMA = ("shelf_day_id:INTEGER,split:STRING,department:STRING,is_promo:BOOLEAN,is_weekend:BOOLEAN,"
          "on_hand:INTEGER,forecast_units:FLOAT,cover_ratio:FLOAT,stockout:BOOLEAN")


def sql(part: str, dataset: str) -> str:
    feats = ", ".join(FEATURES)
    if part == "model":
        return f"""CREATE OR REPLACE MODEL `{dataset}.stockout_xgb`
OPTIONS (
  model_type = 'BOOSTED_TREE_CLASSIFIER',
  xgboost_version = '{XGBOOST_VERSION}',
  input_label_cols = ['stockout'],
  data_split_method = 'NO_SPLIT',
  max_iterations = 40,
  max_tree_depth = 4,
  learn_rate = 0.3,
  early_stop = FALSE
) AS
SELECT {feats}, stockout
FROM `{dataset}.shelf_days`
WHERE split = 'train'
"""
    if part == "predict":
        return f"""SELECT shelf_day_id, predicted_stockout
FROM ML.PREDICT(MODEL `{dataset}.stockout_xgb`,
  (SELECT shelf_day_id, {feats} FROM `{dataset}.shelf_days` WHERE split = 'holdout'))
ORDER BY shelf_day_id
"""
    raise SystemExit(f"unknown part {part}")


def summarise(rows: list[dict], model_pred: dict[int, bool] | None = None) -> dict:
    hold = [r for r in rows if r["split"] == "holdout"]
    out = {
        "train_rows": sum(r["split"] == "train" for r in rows),
        "holdout_rows": len(hold),
        "holdout_stockouts": sum(r["stockout"] for r in hold),
        "naive_rule_accuracy": accuracy(hold, naive_rule),
        "naive_rule_missed_stockouts": sum(r["stockout"] and not naive_rule(r) for r in hold),
        "lift_rule_accuracy": accuracy(hold, lift_rule),
    }
    if model_pred is not None:
        out["xgboost_accuracy"] = accuracy(hold, lambda r: model_pred[r["shelf_day_id"]])
        out["xgboost_missed_stockouts"] = sum(r["stockout"] and not model_pred[r["shelf_day_id"]] for r in hold)
    return out


def parse_predictions(raw: list[dict]) -> dict[int, bool]:
    def b(v):
        return v if isinstance(v, bool) else str(v).lower() == "true"
    return {int(p["shelf_day_id"]): b(p["predicted_stockout"]) for p in raw}


def check(rows: list[dict], model_pred: dict[int, bool]) -> tuple[bool, dict]:
    hold_ids = {r["shelf_day_id"] for r in rows if r["split"] == "holdout"}
    if set(model_pred) != hold_ids:
        return False, {**summarise(rows), "predicted_rows": len(model_pred)}
    s = summarise(rows, model_pred)
    ok = s["xgboost_accuracy"] >= MIN_ACCURACY \
        and s["xgboost_accuracy"] >= s["naive_rule_accuracy"] + MIN_LIFT
    return ok, s


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    rows = generate()
    if mode == "--csv":
        write_csv(rows, sys.argv[2])
    elif mode == "--sql":
        print(sql(sys.argv[2], sys.argv[3]))
    elif mode == "--compare":
        raw = json.loads(Path(sys.argv[2]).read_text() or "[]")
        ok, s = check(rows, parse_predictions(raw))
        if not ok:
            print(f"::error title=Day 21 XGBoost model check failed::{json.dumps(s)}")
            raise SystemExit(1)
        print("::notice title=Day 21 XGBoost 2.1 stockout model::BigQuery ML boosted tree (xgboost_version="
              f"'{XGBOOST_VERSION}') scored the holdout: {json.dumps(s)}")
    else:
        s = summarise(rows)
        (HERE / "result.json").write_text(json.dumps({
            "engine": "pure Python baselines (local) / BigQuery ML BOOSTED_TREE_CLASSIFIER, XGBoost 2.1 (cloud)",
            **s, "cloud_thresholds": {"min_accuracy": MIN_ACCURACY, "min_lift_over_naive": MIN_LIFT}},
            indent=2) + "\n")
        print(json.dumps(s, indent=2))
