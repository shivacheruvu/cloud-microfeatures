"""Day 6 (Google Cloud): is oat milk growing, or just noisy? BigQuery's ML.TREND (Preview), per store.

  python trend.py --local          # centred 7-day moving average + least-squares slope (pure Python); writes result.json
  python trend.py --sql            # prints the BigQuery query (data inlined, nothing to load or clean up)
  python trend.py --compare FILE   # checks BigQuery's JSON output against the local trend

A store is "growing" when its trend rises by at least 1.5 units a week, "declining" when it falls by as much, else
"flat". BigQuery's decomposition is its own (seasonality removed, then smoothed), so the cloud check is:
  * errors:   any non-empty status; a store with no history trend; BigQuery's trend class differs from local
  * warnings: slope off by more than max(1 unit/week, 35%); not 14 forecast rows per store; the forecast trend
              moves the other way from the history trend
"""
from __future__ import annotations

import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
HORIZON = 14
GROWTH = 1.5  # units per week
SLOPE_ABS_TOL, SLOPE_REL_TOL = 1.0, 0.35


def load_rows() -> list[dict]:
    with open(HERE / "data" / "oat_milk_daily.csv", newline="") as f:
        return list(csv.DictReader(f))


def series(rows: list[dict]) -> dict[str, list[tuple[str, int]]]:
    out = defaultdict(list)
    for r in rows:
        out[r["store"]].append((r["sale_date"], int(r["units"])))
    return {s: sorted(v) for s, v in sorted(out.items())}


def bigquery_sql(rows: list[dict]) -> str:
    structs = [f"    STRUCT(DATE '{r['sale_date']}' AS sale_date, '{r['store']}' AS store, {int(r['units'])} AS units)"
               if i == 0 else f"    (DATE '{r['sale_date']}', '{r['store']}', {int(r['units'])})"
               for i, r in enumerate(rows)]
    return (HERE / "trend.sql").read_text().replace("{values}", ",\n".join(structs))


def day_no(d: str) -> int:
    return date.fromisoformat(d).toordinal()


def slope_per_week(points: list[tuple[str, float]]) -> float:
    """Least-squares slope of value against day number, times 7."""
    xs = [day_no(d) for d, _ in points]
    return statistics.linear_regression(xs, [v for _, v in points]).slope * 7


def classify(slope: float) -> str:
    return "growing" if slope >= GROWTH else "declining" if slope <= -GROWTH else "flat"


def local_trend(points: list[tuple[str, int]]) -> list[tuple[str, float]]:
    """Centred 7-day moving average: a full week in every window, so the weekday pattern cancels out."""
    ys = [y for _, y in points]
    return [(points[i][0], statistics.fmean(ys[i - 3:i + 4])) for i in range(3, len(ys) - 3)]


def describe(trend: list[tuple[str, float]]) -> dict:
    slope = slope_per_week(trend)
    return {"class": classify(slope), "slope_per_week": round(slope, 2),
            "first_week_avg": round(statistics.fmean(v for _, v in trend[:7]), 1),
            "last_week_avg": round(statistics.fmean(v for _, v in trend[-7:]), 1)}


def run_local(rows: list[dict]) -> dict[str, dict]:
    return {store: describe(local_trend(pts)) for store, pts in series(rows).items()}


def as_date(v) -> str:
    """'2026-07-03[ 00:00:00 UTC]' or epoch seconds (some bq versions) -> '2026-07-03'."""
    if v is None:
        return ""
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return str(v)[:10]


def read_bq(path: str) -> list[dict]:
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a warning line before the JSON
    try:
        rows = json.loads(text[start:]) if start >= 0 else []
        return [{"store": r.get("store"), "sale_date": as_date(r.get("sale_date")),
                 "type": r.get("time_series_type") or "history", "status": r.get("status") or "",
                 "trend": None if r.get("trend") is None else float(r["trend"])} for r in rows]
    except Exception as e:  # noqa: BLE001 - any shape surprise is reported, redacted, as an annotation
        raw = text[max(start, 0):max(start, 0) + 500].replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {e}; {raw!r}")
        raise SystemExit(1)


def cloud_points(cloud: list[dict], store: str, kind: str) -> list[tuple[str, float]]:
    return sorted((r["sale_date"], r["trend"]) for r in cloud
                  if r["store"] == store and r["type"] == kind and r["trend"] is not None and r["sale_date"])


def compare(cloud: list[dict], rows: list[dict]) -> tuple[list[str], list[str], dict]:
    """Returns (errors, warnings, summary) for BigQuery's trend component against the local trend."""
    local = run_local(rows)
    errors, warnings, summary = [], [], {"rows": len(cloud), "stores": {}}
    bad = [r for r in cloud if r["status"]]
    if bad:
        errors.append(f"status: {bad[0]['status'][:200]}")
    for store, loc in local.items():
        hist = cloud_points(cloud, store, "history")
        if len(hist) < 14:
            errors.append(f"{store}: {len(hist)} history trend row(s)")
            continue
        got = describe(hist)
        fc = cloud_points(cloud, store, "forecast")
        fc_slope = round(slope_per_week(fc), 2) if len(fc) >= 2 else None
        summary["stores"][store] = {**got, "forecast_rows": len(fc), "forecast_slope_per_week": fc_slope,
                                    "forecast_end": round(fc[-1][1], 1) if fc else None}
        if got["class"] != loc["class"]:
            errors.append(f"{store}: BigQuery trend is {got['class']} ({got['slope_per_week']}/wk), "
                          f"local {loc['class']} ({loc['slope_per_week']}/wk)")
        diff = abs(got["slope_per_week"] - loc["slope_per_week"])
        if diff > max(SLOPE_ABS_TOL, SLOPE_REL_TOL * abs(loc["slope_per_week"])):
            warnings.append(f"{store}: slope bq={got['slope_per_week']}/wk local={loc['slope_per_week']}/wk")
        if len(fc) != HORIZON:
            warnings.append(f"{store}: {len(fc)} forecast row(s), expected {HORIZON}")
        if fc_slope is not None and loc["class"] != "flat" and classify(fc_slope) != loc["class"]:
            warnings.append(f"{store}: forecast trend {fc_slope}/wk vs history {loc['class']}")
    return errors, warnings, summary


def summarise(local: dict[str, dict], rows: list[dict]) -> dict:
    ser = series(rows)
    return {"days": len(next(iter(ser.values()))), "stores": len(ser), "trend": local,
            "headline": "; ".join(f"{s}: {v['class']} {v['slope_per_week']:+.1f}/wk" for s, v in local.items())}


if __name__ == "__main__":
    rows = load_rows()
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(bigquery_sql(rows))
    elif mode == "--compare":
        cloud = read_bq(sys.argv[2])
        try:
            errors, warnings, summary = compare(cloud, rows)
        except Exception as e:  # report what BigQuery returned (data rows only: no project or account details)
            print(f"::error title=ML.TREND output not understood::{type(e).__name__}: {e}; "
                  f"{len(cloud)} row(s), first: {json.dumps(cloud[:2])[:700]}")
            raise SystemExit(1)
        for w in warnings:
            print(f"::warning title=ML.TREND vs local::{w[:900]}")
        if errors:
            print(f"::error title=ML.TREND differs from local::{json.dumps(errors)[:900]}")
            raise SystemExit(1)
        print(f"::notice title=Google Cloud day 6::ML.TREND ran on BigQuery and agreed with local "
              f"({json.dumps(summarise(run_local(rows), rows)['headline'])}): {json.dumps(summary)[:700]}")
    else:
        summary = summarise(run_local(rows), rows)
        (HERE / "result.json").write_text(json.dumps({"engine": "pure-Python 7-day moving average + least squares "
                                                      "(local) / BigQuery ML.TREND (cloud)", **summary},
                                                     indent=2) + "\n")
        print(json.dumps(summary, indent=2))
