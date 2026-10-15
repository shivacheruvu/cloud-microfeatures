"""Day 10 (Google Cloud): a promo-aware 14-day forecast of daily strawberry sales with TimesFM 3.0 in BigQuery's
AI.FORECAST, using the promo calendar as a future covariate (new in TimesFM 3.0, Preview 2026-10-07).

  python forecast.py --make-data      # regenerate data/sales.csv (deterministic)
  python forecast.py --local          # pure Python baselines on the held-out 14 days; writes result.json
  python forecast.py --sql            # print the BigQuery query with the promo calendar as a future covariate
  python forecast.py --sql-plain      # print the same forecast without the covariate (univariate TimesFM 3.0)
  python forecast.py --compare OUT [PLAIN_OUT]   # check bq --format=json output against the held-out sales

The cloud check:
  * errors:   a horizon day with no positive forecast; TimesFM-with-promos MAE not better than the seasonal naive
              forecast (same weekday, last week), which ignores promos
  * warnings: TimesFM-with-promos not better than univariate TimesFM 3.0 (the covariate didn't help), or more than
              1.5x the local promo-aware baseline's MAE
TimesFM isn't deterministic across versions, so the check compares errors, not exact values.
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
START = date(2026, 7, 6)          # a Monday
HISTORY_DAYS, HORIZON = 84, 14    # 12 weeks of history, 2 weeks forecast
WEEKDAY_BASE = [100, 95, 98, 105, 130, 160, 140]   # Mon..Sun units of 1 lb strawberries at one store
PROMO_LIFT = 1.6
PROMO_OFFSETS = {3, 4, 15, 26, 27, 37, 49, 50, 60, 72, 73,      # history: 11 promo days, mostly mid-week
                 85, 86, 94}                                     # horizon: Tue 9/29, Wed 9/30, Thu 10/8
WARN_RATIO = 1.5


def make_data() -> list[dict]:
    seed, rows = 20261014, []
    for i in range(HISTORY_DAYS + HORIZON):
        seed = (seed * 1103515245 + 12345) % 2**31            # LCG: same numbers everywhere
        noise = 1 + ((seed / 2**31) - 0.5) * 0.12              # +-6%
        d = START + timedelta(days=i)
        promo = i in PROMO_OFFSETS
        units = round((WEEKDAY_BASE[d.weekday()] + 0.2 * i) * (PROMO_LIFT if promo else 1) * noise, 1)
        rows.append({"day": d.isoformat(), "units": units, "on_promo": int(promo),
                     "role": "history" if i < HISTORY_DAYS else "holdout"})
    return rows


def load_rows() -> list[dict]:
    with open(DATA, newline="") as f:
        return [{"day": r["day"], "units": float(r["units"]), "on_promo": int(r["on_promo"]), "role": r["role"]}
                for r in csv.DictReader(f)]


def split(rows):
    return [r for r in rows if r["role"] == "history"], [r for r in rows if r["role"] == "holdout"]


def mae(pred: dict[str, float], actual: list[dict]) -> float:
    return sum(abs(pred[r["day"]] - r["units"]) for r in actual) / len(actual)


# ---------- local baselines
def run_local(rows: list[dict]) -> dict:
    hist, hold = split(rows)
    by_day = {r["day"]: r for r in hist}
    # seasonal naive: the same weekday in the last history week (ignores promos)
    last_week = {date.fromisoformat(r["day"]).weekday(): r["units"] for r in hist[-7:]}
    naive = {r["day"]: last_week[date.fromisoformat(r["day"]).weekday()] for r in hold}
    # promo-aware baseline: weekday mean of the last 4 non-promo weeks x the average promo lift seen in history
    wk: dict[int, list[float]] = {}
    for r in hist[-28:]:
        if not r["on_promo"]:
            wk.setdefault(date.fromisoformat(r["day"]).weekday(), []).append(r["units"])
    wk_mean = {k: sum(v) / len(v) for k, v in wk.items()}
    lifts = []
    for r in hist:
        if r["on_promo"]:
            d = date.fromisoformat(r["day"])
            nb = [by_day[x.isoformat()]["units"] for x in (d - timedelta(7), d + timedelta(7))
                  if x.isoformat() in by_day and not by_day[x.isoformat()]["on_promo"]]
            if nb:
                lifts.append(r["units"] / (sum(nb) / len(nb)))
    lift = sum(lifts) / len(lifts)
    model = {r["day"]: wk_mean[date.fromisoformat(r["day"]).weekday()] * (lift if r["on_promo"] else 1)
             for r in hold}
    promo_days = [r for r in hold if r["on_promo"]]
    return {"naive": naive, "model": model, "lift": round(lift, 2),
            "mae_naive": round(mae(naive, hold), 1), "mae_model": round(mae(model, hold), 1),
            "mae_naive_promo_days": round(mae(naive, promo_days), 1),
            "mae_model_promo_days": round(mae(model, promo_days), 1)}


# ---------- BigQuery
def _struct(r: dict, with_units: bool) -> str:
    units = f"{r['units']}" if with_units else "CAST(NULL AS FLOAT64)"
    return f"STRUCT(DATE '{r['day']}' AS day, {units} AS units, {float(r['on_promo'])} AS on_promo)"


def bigquery_sql(rows: list[dict], with_promos: bool = True) -> str:
    hist, hold = split(rows)
    structs = [_struct(r, True) for r in hist]
    if with_promos:  # horizon rows: promo calendar known, units NULL (the held-out sales are never sent)
        structs += [_struct(r, False) for r in hold]
    name = "forecast.sql" if with_promos else "forecast_plain.sql"
    return (HERE / name).read_text().replace("{sales}", ",\n    ".join(structs)).replace("{horizon}", str(HORIZON))


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


def cloud_forecast(cloud: list[dict], hold: list[dict]) -> dict[str, float | None]:
    """Map output rows to horizon days. Reads the multivariate shape (`day`, `units` STRUCT with `value`) and falls
    back to the univariate one (`forecast_timestamp`, `forecast_value`)."""
    days = {r["day"] for r in hold}
    out: dict[str, float | None] = {}
    for c in cloud:
        stamp = next((str(c[k])[:10] for k in ("day", "forecast_timestamp", "time_series_timestamp")
                      if c.get(k) is not None), None)
        if stamp not in days:
            continue  # history rows, if any are echoed back
        u = c.get("units")
        v = u.get("value") if isinstance(u, dict) else (u if u is not None else c.get("forecast_value"))
        out[stamp] = _num(v)
    return out


def compare(cloud: list[dict], rows: list[dict], plain: list[dict] | None = None):
    hist, hold = split(rows)
    local = run_local(rows)
    pred = cloud_forecast(cloud, hold)
    errors, warnings = [], []
    for r in hold:
        v = pred.get(r["day"])
        if v is None or v <= 0:
            errors.append(f"{r['day']}: no positive forecast ({v})")
    if errors:
        return errors, warnings, {}
    promo_days = [r for r in hold if r["on_promo"]]
    m, mp = round(mae(pred, hold), 1), round(mae(pred, promo_days), 1)
    summary = {"mae_timesfm_promos": m, "mae_timesfm_promos_promo_days": mp,
               "forecast": {k: round(v, 1) for k, v in sorted(pred.items())}}
    if m >= local["mae_naive"]:
        errors.append(f"TimesFM 3.0 with promos MAE {m} is not better than seasonal naive {local['mae_naive']}")
    if m > WARN_RATIO * local["mae_model"]:
        warnings.append(f"TimesFM 3.0 with promos MAE {m} vs local promo-aware baseline {local['mae_model']}")
    if plain is not None:
        pp = cloud_forecast(plain, hold)
        if all(pp.get(r["day"]) is not None for r in hold):
            summary["mae_timesfm_plain"] = round(mae(pp, hold), 1)
            summary["mae_timesfm_plain_promo_days"] = round(mae(pp, promo_days), 1)
            if summary["mae_timesfm_plain"] <= m:
                warnings.append(f"the promo covariate didn't help: univariate MAE {summary['mae_timesfm_plain']} "
                                f"vs {m} with promos")
        else:
            warnings.append(f"univariate TimesFM output not understood: {json.dumps(plain[:1])[:300]}")
    return errors, warnings, summary


def summarise(rows: list[dict], local: dict) -> dict:
    hist, hold = split(rows)
    return {
        "history_days": len(hist), "horizon_days": len(hold), "promo_days_in_horizon": sum(r["on_promo"] for r in hold),
        "history_promo_lift": local["lift"],
        "mae_seasonal_naive": local["mae_naive"], "mae_promo_aware_baseline": local["mae_model"],
        "mae_seasonal_naive_promo_days": local["mae_naive_promo_days"],
        "mae_promo_aware_baseline_promo_days": local["mae_model_promo_days"],
        "table": [{"day": r["day"], "weekday": date.fromisoformat(r["day"]).strftime("%a"), "on_promo": r["on_promo"],
                   "actual": r["units"], "seasonal_naive": round(local["naive"][r["day"]], 1),
                   "promo_aware_baseline": round(local["model"][r["day"]], 1)} for r in hold],
        "headline": f"14-day strawberry forecast, 3 promo days: promo-aware baseline MAE {local['mae_model']} vs "
                    f"seasonal naive {local['mae_naive']} units/day; TimesFM 3.0 with the promo calendar must beat "
                    "naive",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        with open(DATA, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["day", "units", "on_promo", "role"])
            w.writeheader()
            w.writerows(make_data())
        raise SystemExit(0)
    rows = load_rows()
    if mode == "--sql":
        print(bigquery_sql(rows))
    elif mode == "--sql-plain":
        print(bigquery_sql(rows, with_promos=False))
    elif mode == "--compare":
        cloud = read_bq(sys.argv[2])
        plain = read_bq(sys.argv[3]) if len(sys.argv) > 3 else None
        try:
            errors, warnings, summary = compare(cloud, rows, plain)
        except Exception as e:  # report what BigQuery returned (data rows only: no project or account details)
            print(f"::error title=AI.FORECAST output not understood::{type(e).__name__}: {e}; "
                  f"{len(cloud)} row(s), first: {json.dumps(cloud[:2])[:700]}")
            raise SystemExit(1)
        for w in warnings:
            print(f"::warning title=AI.FORECAST vs local::{w[:900]}")
        if errors:
            print(f"::error title=AI.FORECAST check failed::{json.dumps(errors)[:900]}; "
                  f"first row {json.dumps(cloud[:1])[:400]}")
            raise SystemExit(1)
        local = run_local(rows)
        plain_txt = (f", univariate TimesFM 3.0 {summary['mae_timesfm_plain']} "
                     f"(promo days {summary['mae_timesfm_plain_promo_days']})" if "mae_timesfm_plain" in summary else "")
        print(f"::notice title=Google Cloud day 10::AI.FORECAST with TimesFM 3.0 and the promo calendar as a future "
              f"covariate ran on BigQuery: MAE {summary['mae_timesfm_promos']} units/day (promo days "
              f"{summary['mae_timesfm_promos_promo_days']}){plain_txt}; local seasonal naive {local['mae_naive']} "
              f"(promo days {local['mae_naive_promo_days']}), promo-aware baseline {local['mae_model']}; "
              f"forecast {json.dumps(summary['forecast'])[:500]}")
    else:
        local = run_local(rows)
        summary = summarise(rows, local)
        (HERE / "result.json").write_text(json.dumps({"engine": "pure-Python seasonal naive and promo-aware baseline "
                                                      "(local) / BigQuery AI.FORECAST with TimesFM 3.0 and a future "
                                                      "covariate (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
