"""Day 52 (Google Cloud): a 14-day baking-aisle forecast (flour, butter, eggs) for two stores, all three items in ONE
BigQuery AI.FORECAST call with TimesFM 3.0 multi-target mode (`target_cols` with several columns, Preview 2026-10-07).

  python forecast.py --make-data      # regenerate data/sales.csv (deterministic)
  python forecast.py --local          # pure Python baselines on the held-out 14 days; writes result.json
  python forecast.py --sql            # print the multi-target BigQuery query (TimesFM 3.0, target_cols)
  python forecast.py --sql-univariate # print the same six series as univariate forecasts (TimesFM 2.5, data_col)
  python forecast.py --compare OUT [UNI_OUT]   # check bq --format=json output against the held-out sales

The cloud check (TimesFM isn't bit-for-bit reproducible, so it compares errors, not exact values):
  * errors:   any of the 84 (2 stores x 3 items x 14 days) forecasts missing or not positive, an interval that doesn't
              hold its own forecast, a non-empty ai_forecast_status, or a WAPE more than 1.25x the seasonal naive one
              (that would mean the forecast is broken, not just a bit worse)
  * warnings: multi-target WAPE not better than seasonal naive; 90% intervals covering under 70% of actuals; the
              univariate TimesFM 2.5 query failing or not understood
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
START = date(2026, 7, 6)            # a Monday
HISTORY_DAYS, HORIZON = 112, 14     # 16 weeks of history (TimesFM needs >= 64 points), 2 weeks held out
ITEMS = ("flour", "butter", "eggs")
STORES = {"north": 1.0, "south": 0.7}
# Mon..Sun units at the north store: weekend baking peaks, eggs sell every day
WEEKDAY_BASE = {
    "flour": [40, 38, 40, 44, 55, 80, 70],
    "butter": [60, 58, 60, 66, 80, 110, 95],
    "eggs": [150, 140, 145, 150, 170, 210, 190],
}
TREND = {"flour": 0.0025, "butter": 0.002, "eggs": 0.0005}   # fall baking season: share of base added per day
BREAK_ERROR_RATIO = 1.25
MIN_COVERAGE = 0.70


def _lcg(seed: int) -> tuple[int, float]:
    seed = (seed * 1103515245 + 12345) % 2**31
    return seed, seed / 2**31


def make_data() -> list[dict]:
    seed, rows = 20261125, []
    for store, scale in STORES.items():
        for i in range(HISTORY_DAYS + HORIZON):
            d = START + timedelta(days=i)
            seed, u = _lcg(seed)
            traffic = 1 + (u - 0.5) * 0.10            # +-5% store traffic, shared by all three items that day
            row = {"store": store, "day": d.isoformat(), "role": "history" if i < HISTORY_DAYS else "holdout"}
            for item in ITEMS:
                seed, u = _lcg(seed)
                noise = 1 + (u - 0.5) * 0.08          # +-4% item noise
                base = WEEKDAY_BASE[item][d.weekday()] * scale
                row[item] = round(base * (1 + TREND[item] * i) * traffic * noise, 1)
            rows.append(row)
    return rows


def load_rows() -> list[dict]:
    with open(DATA, newline="") as f:
        return [{"store": r["store"], "day": r["day"], "role": r["role"], **{k: float(r[k]) for k in ITEMS}}
                for r in csv.DictReader(f)]


def split(rows):
    return [r for r in rows if r["role"] == "history"], [r for r in rows if r["role"] == "holdout"]


def actuals(rows) -> dict[tuple[str, str, str], float]:
    _, hold = split(rows)
    return {(r["store"], item, r["day"]): r[item] for r in hold for item in ITEMS}


def wape(pred: dict, actual: dict) -> float:
    """Weighted absolute percentage error over all series: sum |error| / sum actual (items differ in scale)."""
    return 100 * sum(abs(pred[k] - v) for k, v in actual.items()) / sum(actual.values())


def wape_by_item(pred: dict, actual: dict) -> dict[str, float]:
    return {item: round(wape({k: pred[k] for k in actual if k[1] == item},
                             {k: v for k, v in actual.items() if k[1] == item}), 2) for item in ITEMS}


# ---------- local baselines
def run_local(rows: list[dict]) -> dict:
    hist, hold = split(rows)
    act = actuals(rows)
    naive, mean4 = {}, {}
    for store in STORES:
        h = [r for r in hist if r["store"] == store]
        for item in ITEMS:
            last_week = {date.fromisoformat(r["day"]).weekday(): r[item] for r in h[-7:]}
            by_wd: dict[int, list[float]] = {}
            for r in h[-28:]:
                by_wd.setdefault(date.fromisoformat(r["day"]).weekday(), []).append(r[item])
            for r in hold:
                if r["store"] != store:
                    continue
                wd = date.fromisoformat(r["day"]).weekday()
                naive[(store, item, r["day"])] = last_week[wd]
                mean4[(store, item, r["day"])] = sum(by_wd[wd]) / len(by_wd[wd])
    return {"naive": naive, "mean4": mean4,
            "wape_naive": round(wape(naive, act), 2), "wape_mean4": round(wape(mean4, act), 2),
            "wape_naive_by_item": wape_by_item(naive, act), "wape_mean4_by_item": wape_by_item(mean4, act)}


# ---------- BigQuery
def _struct(r: dict) -> str:
    items = ", ".join(f"{r[i]} AS {i}" for i in ITEMS)
    return f"STRUCT('{r['store']}' AS store, DATE '{r['day']}' AS day, {items})"


def bigquery_sql(rows: list[dict], univariate: bool = False) -> str:
    hist, _ = split(rows)   # only history is sent: the held-out sales never leave the runner
    name = "forecast_univariate.sql" if univariate else "forecast_multi.sql"
    return ((HERE / name).read_text().replace("{sales}", ",\n    ".join(_struct(r) for r in hist))
            .replace("{horizon}", str(HORIZON)))


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


def parse_multi(cloud: list[dict]) -> tuple[dict, dict, list[str]]:
    """Wide multi-target rows -> {(store, item, day): value}, {(store, item, day): (lo, hi)}, statuses."""
    pred, band, statuses = {}, {}, []
    for c in cloud:
        day = str(c.get("day"))[:10]
        if c.get("ai_forecast_status"):
            statuses.append(f"{c.get('store')} {day}: {str(c['ai_forecast_status'])[:200]}")
        for item in ITEMS:
            key = (c.get("store"), item, day)
            pred[key] = _num(c.get(item))
            band[key] = (_num(c.get(f"{item}_lo")), _num(c.get(f"{item}_hi")))
    return pred, band, statuses


def parse_univariate(cloud: list[dict]) -> dict:
    return {(c.get("store"), c.get("item"), str(c.get("day"))[:10]): _num(c.get("value")) for c in cloud}


def compare(cloud: list[dict], rows: list[dict], uni: list[dict] | None = None):
    act = actuals(rows)
    local = run_local(rows)
    pred, band, statuses = parse_multi(cloud)
    errors, warnings = [], []
    if statuses:
        errors.append(f"ai_forecast_status not empty: {statuses[:3]}")
    missing = [k for k in act if pred.get(k) is None or pred[k] <= 0]
    if missing:
        errors.append(f"{len(missing)} of {len(act)} forecasts missing or not positive, e.g. {missing[:3]}")
        return errors, warnings, {}
    bad_band = [k for k in act if None in band[k] or not band[k][0] <= pred[k] <= band[k][1]]
    if bad_band:
        errors.append(f"{len(bad_band)} intervals don't hold their forecast, e.g. {bad_band[:3]}")
    w = round(wape(pred, act), 2)
    covered = sum(1 for k, v in act.items() if None not in band[k] and band[k][0] <= v <= band[k][1])
    summary = {"forecasts": len(act), "wape_multi": w, "wape_multi_by_item": wape_by_item(pred, act),
               "coverage_90": round(covered / len(act), 3)}
    if w > BREAK_ERROR_RATIO * local["wape_naive"]:
        errors.append(f"multi-target WAPE {w}% is over {BREAK_ERROR_RATIO}x seasonal naive {local['wape_naive']}%")
    elif w >= local["wape_naive"]:
        warnings.append(f"multi-target WAPE {w}% is not better than seasonal naive {local['wape_naive']}%")
    if summary["coverage_90"] < MIN_COVERAGE:
        warnings.append(f"90% intervals hold only {covered}/{len(act)} actuals")
    if uni is not None:
        up = parse_univariate(uni)
        if all(up.get(k) is not None for k in act):
            summary["wape_univariate_2_5"] = round(wape(up, act), 2)
            summary["wape_univariate_2_5_by_item"] = wape_by_item(up, act)
        else:
            warnings.append(f"univariate TimesFM 2.5 output not understood: {json.dumps(uni[:1])[:300]}")
    return errors, warnings, summary


def summarise(rows: list[dict], local: dict) -> dict:
    hist, hold = split(rows)
    return {
        "stores": len(STORES), "items": list(ITEMS), "history_days": len(hist) // len(STORES),
        "horizon_days": len(hold) // len(STORES), "forecasts_checked": len(hold) * len(ITEMS),
        "wape_seasonal_naive": local["wape_naive"], "wape_four_week_weekday_mean": local["wape_mean4"],
        "wape_seasonal_naive_by_item": local["wape_naive_by_item"],
        "wape_four_week_weekday_mean_by_item": local["wape_mean4_by_item"],
        "headline": f"3 items x 2 stores in one TimesFM 3.0 call; local baselines: seasonal naive WAPE "
                    f"{local['wape_naive']}%, 4-week weekday mean {local['wape_mean4']}%",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        DATA.parent.mkdir(exist_ok=True)
        with open(DATA, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["store", "day", *ITEMS, "role"])
            w.writeheader()
            w.writerows(make_data())
        raise SystemExit(0)
    rows = load_rows()
    if mode == "--sql":
        print(bigquery_sql(rows))
    elif mode == "--sql-univariate":
        print(bigquery_sql(rows, univariate=True))
    elif mode == "--compare":
        cloud = read_bq(sys.argv[2])
        uni = read_bq(sys.argv[3]) if len(sys.argv) > 3 else None
        try:
            errors, warnings, summary = compare(cloud, rows, uni)
        except Exception as e:  # report what BigQuery returned (data rows only: no project or account details)
            print(f"::error title=AI.FORECAST output not understood::{type(e).__name__}: {e}; "
                  f"{len(cloud)} row(s), first: {json.dumps(cloud[:2])[:700]}")
            raise SystemExit(1)
        for w in warnings:
            print(f"::warning title=Day 52 multi-target forecast::{w[:900]}")
        if errors:
            print(f"::error title=Day 52 multi-target forecast check failed::{json.dumps(errors)[:900]}; "
                  f"first row {json.dumps(cloud[:1])[:400]}")
            raise SystemExit(1)
        local = run_local(rows)
        uni_txt = (f"; univariate TimesFM 2.5 per series {summary['wape_univariate_2_5']}% "
                   f"{json.dumps(summary['wape_univariate_2_5_by_item'])}" if "wape_univariate_2_5" in summary else "")
        print(f"::notice title=Google Cloud day 52::AI.FORECAST with TimesFM 3.0 target_cols => [flour, butter, eggs] "
              f"forecast 2 stores x 3 items x 14 days ({summary['forecasts']} values) in one call: WAPE "
              f"{summary['wape_multi']}% {json.dumps(summary['wape_multi_by_item'])}, 90% intervals hold "
              f"{summary['coverage_90']:.0%} of actuals{uni_txt}; local seasonal naive {local['wape_naive']}%, "
              f"4-week weekday mean {local['wape_mean4']}%")
    else:
        local = run_local(rows)
        summary = summarise(rows, local)
        (HERE / "result.json").write_text(json.dumps({
            "engine": "pure-Python seasonal naive and 4-week weekday mean (local) / BigQuery AI.FORECAST with "
                      "TimesFM 3.0 multi-target target_cols (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
