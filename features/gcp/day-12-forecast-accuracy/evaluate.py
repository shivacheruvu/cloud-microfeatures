"""Day 12 (Google Cloud): how accurate is a zero-shot forecast for our items? BigQuery's AI.EVALUATE scores TimesFM 3.0
(Preview 2026-10-07: TimesFM 3.0 now also works in AI.EVALUATE) next to TimesFM 2.5 on 14 held-out days of sales for
three grocery items, and the runner compares both with a seasonal-naive baseline ("same weekday last week").

  python evaluate.py --make-data              # regenerate data/sales.csv (deterministic)
  python evaluate.py --local                  # seasonal-naive baseline metrics in pure Python; writes result.json
  python evaluate.py --sql [MODELS]           # print the BigQuery query (default 'TimesFM 3.0,TimesFM 2.5')
  python evaluate.py --compare OUT [MODELS]   # check bq --format=json output

The cloud check:
  * errors:   a (model, item) row missing, a non-empty ai_evaluate_status, or a non-finite / negative MAE or RMSE;
              any MAE above the larger of 3x the seasonal-naive MAE and 10% of mean sales (a sign the call scored
              the wrong thing)
  * warnings: TimesFM 3.0 with a higher MAE than the seasonal-naive baseline for an item
TimesFM isn't deterministic across versions, so the check bounds the metrics rather than matching them exactly.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "sales.csv"
START = date(2026, 6, 1)                 # a Monday
HISTORY_DAYS, HORIZON = 112, 14          # 16 weeks of history, 2 weeks held out
ITEMS = {  # Mon..Sun daily units at one store, weekly growth
    "bananas (lb)": ([180, 170, 175, 185, 210, 260, 230], 0.000),
    "milk 1 gal": ([120, 115, 118, 122, 140, 165, 150], -0.002),
    "oat milk 64 oz": ([22, 21, 22, 24, 28, 34, 30], 0.015),   # growing 1.5% a week
}
MODELS = ["TimesFM 3.0", "TimesFM 2.5"]
METRICS = ["mean_absolute_error", "mean_squared_error", "root_mean_squared_error", "mean_absolute_percentage_error",
           "symmetric_mean_absolute_percentage_error", "mean_absolute_scaled_error"]
MAX_VS_NAIVE = 3.0
FLOOR_SHARE = 0.10        # ... or 10% of mean held-out sales, whichever is larger


def sanity_limit(naive: dict) -> float:
    return max(MAX_VS_NAIVE * naive["mean_absolute_error"], FLOOR_SHARE * naive["mean_actual"])


def make_data() -> list[dict]:
    seed, rows = 20261016, []
    for name, (base, growth) in ITEMS.items():
        for i in range(HISTORY_DAYS + HORIZON):
            seed = (seed * 1103515245 + 12345) % 2**31          # LCG: same numbers everywhere
            noise = 1 + ((seed / 2**31) - 0.5) * 0.12            # +-6%
            d = START + timedelta(days=i)
            rows.append({"item": name, "day": d.isoformat(),
                         "units": round(base[d.weekday()] * (1 + growth) ** (i / 7) * noise),
                         "role": "history" if i < HISTORY_DAYS else "actual"})
    return rows


def load_rows() -> list[dict]:
    with open(DATA, newline="") as f:
        return [{**r, "units": int(r["units"])} for r in csv.DictReader(f)]


def split(rows):
    return [r for r in rows if r["role"] == "history"], [r for r in rows if r["role"] == "actual"]


# ---------- local baseline: seasonal naive (each held-out day = the same weekday in the last history week)
def run_local(rows: list[dict]) -> dict[str, dict]:
    hist, actual = split(rows)
    out = {}
    for item in ITEMS:
        last_week = [r["units"] for r in hist if r["item"] == item][-7:]
        act = [r for r in actual if r["item"] == item]
        errs = [(r["units"], last_week[i % 7]) for i, r in enumerate(act)]
        mae = sum(abs(a - f) for a, f in errs) / len(errs)
        out[item] = {"mean_absolute_error": round(mae, 3),
                     "root_mean_squared_error": round(math.sqrt(sum((a - f) ** 2 for a, f in errs) / len(errs)), 3),
                     "mean_absolute_percentage_error": round(100 * sum(abs(a - f) / a for a, f in errs) / len(errs), 2),
                     "mean_actual": round(sum(a for a, _ in errs) / len(errs), 1)}
    return out


# ---------- BigQuery
def _struct(r: dict) -> str:
    return f"STRUCT('{r['item']}' AS item, DATE '{r['day']}' AS day, {float(r['units'])} AS units)"


def _select(model: str) -> str:
    return (f"SELECT '{model}' AS model, * FROM AI.EVALUATE(\n  TABLE sales_history,\n  TABLE sales_actual,\n"
            f"  data_col => 'units',\n  timestamp_col => 'day',\n  id_cols => ['item'],\n  model => '{model}',\n"
            f"  horizon => {HORIZON}\n)")


def bigquery_sql(rows: list[dict], models: list[str] = MODELS) -> str:
    hist, actual = split(rows)
    return ((HERE / "evaluate.sql").read_text()
            .replace("{history}", ",\n    ".join(_struct(r) for r in hist))
            .replace("{actual}", ",\n    ".join(_struct(r) for r in actual))
            .replace("{selects}", "\nUNION ALL\n".join(_select(m) for m in models)))


def read_bq(path: str) -> list[dict]:
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a warning line before the JSON
    try:
        return json.loads(text[start:]) if start >= 0 else []
    except Exception as e:  # noqa: BLE001 - any shape surprise is reported, redacted, as an annotation
        raw = text[max(start, 0):max(start, 0) + 500].replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {e}; {raw!r}")
        raise SystemExit(1)


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def compare(cloud: list[dict], local: dict[str, dict], models: list[str] = MODELS):
    got = {(c.get("model"), c.get("item")): c for c in cloud}
    errors, warnings, table = [], [], {}
    for m in models:
        for item, naive in local.items():
            c = got.get((m, item))
            if c is None:
                errors.append(f"{m} / {item}: no output row")
                continue
            if c.get("ai_evaluate_status"):
                errors.append(f"{m} / {item}: status {str(c['ai_evaluate_status'])[:120]!r}")
                continue
            mae, rmse = _num(c.get("mean_absolute_error")), _num(c.get("root_mean_squared_error"))
            if mae is None or rmse is None or mae < 0 or rmse < 0:
                errors.append(f"{m} / {item}: MAE {c.get('mean_absolute_error')!r}, RMSE "
                              f"{c.get('root_mean_squared_error')!r}")
                continue
            if mae > sanity_limit(naive):
                errors.append(f"{m} / {item}: MAE {mae:.1f} is above {sanity_limit(naive):.1f} (the larger of "
                              f"{MAX_VS_NAIVE:g}x the seasonal-naive MAE and {int(100 * FLOOR_SHARE)}% of mean sales)")
            if m == models[0] and mae > naive["mean_absolute_error"]:
                warnings.append(f"{m} / {item}: MAE {mae:.1f} worse than seasonal naive "
                                f"{naive['mean_absolute_error']:.1f}")
            table.setdefault(item, {"seasonal_naive_mae": naive["mean_absolute_error"]})[m] = {
                "mae": round(mae, 2), "rmse": round(rmse, 2),
                "mape": None if _num(c.get("mean_absolute_percentage_error")) is None
                else round(_num(c["mean_absolute_percentage_error"]), 2),
                "mase": None if _num(c.get("mean_absolute_scaled_error")) is None
                else round(_num(c["mean_absolute_scaled_error"]), 3)}
    return errors, warnings, table


def summarise(rows: list[dict], local: dict[str, dict]) -> dict:
    return {
        "items": len(ITEMS), "history_days": HISTORY_DAYS, "horizon_days": HORIZON, "models": MODELS,
        "local_baseline": "seasonal naive: each held-out day = the same weekday in the last history week",
        "table": [{"item": k, "mean_actual_units": v["mean_actual"], "naive_mae": v["mean_absolute_error"],
                   "naive_rmse": v["root_mean_squared_error"], "naive_mape_pct": v["mean_absolute_percentage_error"]}
                  for k, v in local.items()],
        "headline": f"{len(ITEMS)} items x {HORIZON} held-out days scored with AI.EVALUATE (TimesFM 3.0 vs 2.5); "
                    "seasonal-naive MAPE " + ", ".join(f"{k} {v['mean_absolute_percentage_error']}%"
                                                       for k, v in local.items()),
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        with open(DATA, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["item", "day", "units", "role"])
            w.writeheader()
            w.writerows(make_data())
        raise SystemExit(0)
    rows = load_rows()
    if mode == "--sql":
        print(bigquery_sql(rows, sys.argv[2].split(",") if len(sys.argv) > 2 else MODELS))
    elif mode == "--compare":
        cloud = read_bq(sys.argv[2])
        models = sys.argv[3].split(",") if len(sys.argv) > 3 else MODELS
        try:
            errors, warnings, table = compare(cloud, run_local(rows), models)
        except Exception as e:  # report what BigQuery returned (data rows only: no project or account details)
            print(f"::error title=AI.EVALUATE output not understood::{type(e).__name__}: {e}; "
                  f"{len(cloud)} row(s), first: {json.dumps(cloud[:2])[:700]}")
            raise SystemExit(1)
        for w in warnings:
            print(f"::warning title=AI.EVALUATE vs seasonal naive::{w[:900]}")
        if errors:
            print(f"::error title=AI.EVALUATE check failed::{json.dumps(errors)[:800]}; "
                  f"first row {json.dumps(cloud[:1])[:400]}")
            raise SystemExit(1)
        print(f"::notice title=Google Cloud day 12::AI.EVALUATE ran on BigQuery with {' and '.join(models)} over "
              f"{len(ITEMS)} items x {HORIZON} held-out days; per item (MAE, RMSE, MAPE, MASE vs seasonal-naive MAE): "
              f"{json.dumps(table)[:900]}")
    else:
        local = run_local(rows)
        summary = summarise(rows, local)
        (HERE / "result.json").write_text(json.dumps({"engine": "pure-Python seasonal-naive baseline (local) / "
                                                      "BigQuery AI.EVALUATE with TimesFM 3.0 and 2.5 (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
