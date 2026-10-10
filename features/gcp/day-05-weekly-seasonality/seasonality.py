"""Day 5 (Google Cloud): which weekday sells the most bread, per store? BigQuery's ML.SEASONALITY (Preview).

  python seasonality.py --local          # classical decomposition (pure Python); writes result.json
  python seasonality.py --sql            # prints the BigQuery query (data inlined, nothing to load or clean up)
  python seasonality.py --compare FILE   # checks BigQuery's JSON output against the local weekly profile

Local method: a centred 7-day moving average is the trend; each weekday's average distance from it, centred to sum
to zero, is the weekly profile. BigQuery's decomposition is its own (ARIMA_PLUS-style), so the cloud check is:
  * errors:   any non-empty status; a store missing or with no weekly component; BigQuery's peak weekday (its
              weekly component averaged per weekday) differs from the local peak weekday
  * warnings: trough weekday differs; profile correlation below 0.95; peak-to-trough amplitude off by more than 30%
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
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MIN_CORR = 0.95
AMP_TOL = 0.30


def load_rows() -> list[dict]:
    with open(HERE / "data" / "bread_daily.csv", newline="") as f:
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
    return (HERE / "seasonality.sql").read_text().replace("{values}", ",\n".join(structs))


def weekday(d: str) -> str:
    return DAYS[date.fromisoformat(d).weekday()]


def weekly_profile(points: list[tuple[str, int]]) -> dict[str, float]:
    """Average detrended value per weekday (centred 7-day moving average as trend), centred to sum to zero."""
    ys = [y for _, y in points]
    by_day = defaultdict(list)
    for i in range(3, len(ys) - 3):
        by_day[weekday(points[i][0])].append(ys[i] - statistics.fmean(ys[i - 3:i + 4]))
    raw = {d: statistics.fmean(by_day[d]) for d in DAYS}
    mean = statistics.fmean(raw.values())
    return {d: raw[d] - mean for d in DAYS}


def describe(profile: dict[str, float]) -> dict:
    peak, trough = max(profile, key=profile.get), min(profile, key=profile.get)
    return {"peak_day": peak, "trough_day": trough, "peak_vs_avg": round(profile[peak], 1),
            "amplitude": round(profile[peak] - profile[trough], 1),
            "profile": {d: round(v, 1) for d, v in profile.items()}}


def run_local(rows: list[dict]) -> dict[str, dict]:
    return {store: describe(weekly_profile(pts)) for store, pts in series(rows).items()}


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
                 "weekly": None if r.get("weekly") is None else float(r["weekly"])} for r in rows]
    except Exception as e:  # noqa: BLE001 - any shape surprise is reported, redacted, as an annotation
        raw = text[max(start, 0):max(start, 0) + 500].replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {e}; {raw!r}")
        raise SystemExit(1)


def cloud_profile(cloud: list[dict], store: str) -> dict[str, float] | None:
    by_day = defaultdict(list)
    for r in cloud:
        if r["store"] == store and r["type"] == "history" and r["weekly"] is not None and r["sale_date"]:
            by_day[weekday(r["sale_date"])].append(r["weekly"])
    if set(by_day) != set(DAYS):
        return None
    return {d: statistics.fmean(by_day[d]) for d in DAYS}


def compare(cloud: list[dict], rows: list[dict]) -> tuple[list[str], list[str], dict]:
    """Returns (errors, warnings, summary) for BigQuery's weekly component against the local profile."""
    local = run_local(rows)
    errors, warnings, summary = [], [], {"rows": len(cloud), "stores": {}}
    bad = [r for r in cloud if r["status"]]
    if bad:
        errors.append(f"status: {bad[0]['status'][:200]}")
    for store, loc in local.items():
        prof = cloud_profile(cloud, store)
        if prof is None:
            errors.append(f"{store}: no weekly component for every weekday "
                          f"({sum(1 for r in cloud if r['store'] == store)} row(s))")
            continue
        got = describe(prof)
        corr = statistics.correlation([prof[d] for d in DAYS], [loc["profile"][d] for d in DAYS])
        summary["stores"][store] = {"peak_day": got["peak_day"], "trough_day": got["trough_day"],
                                    "amplitude": got["amplitude"], "correlation": round(corr, 3)}
        if got["peak_day"] != loc["peak_day"]:
            errors.append(f"{store}: BigQuery peak weekday {got['peak_day']} != local {loc['peak_day']} "
                          f"(bq profile {got['profile']})")
        if got["trough_day"] != loc["trough_day"]:
            warnings.append(f"{store}: trough weekday bq={got['trough_day']} local={loc['trough_day']}")
        if corr < MIN_CORR:
            warnings.append(f"{store}: profile correlation {corr:.3f} < {MIN_CORR} (bq {got['profile']})")
        if abs(got["amplitude"] / loc["amplitude"] - 1) > AMP_TOL:
            warnings.append(f"{store}: amplitude bq={got['amplitude']} local={loc['amplitude']}")
    return errors, warnings, summary


def summarise(local: dict[str, dict], rows: list[dict]) -> dict:
    ser = series(rows)
    return {"days": len(next(iter(ser.values()))), "stores": len(ser), "weekly": local,
            "headline": "; ".join(f"{s}: {v['peak_day']} +{v['peak_vs_avg']:.0f} loaves" for s, v in local.items())}


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
            print(f"::error title=ML.SEASONALITY output not understood::{type(e).__name__}: {e}; "
                  f"{len(cloud)} row(s), first: {json.dumps(cloud[:2])[:700]}")
            raise SystemExit(1)
        for w in warnings:
            print(f"::warning title=ML.SEASONALITY vs local::{w[:900]}")
        if errors:
            print(f"::error title=ML.SEASONALITY differs from local::{json.dumps(errors)[:900]}")
            raise SystemExit(1)
        print(f"::notice title=Google Cloud day 5::ML.SEASONALITY ran on BigQuery and agreed with local "
              f"({json.dumps(summarise(run_local(rows), rows)['headline'])}): {json.dumps(summary)[:700]}")
    else:
        summary = summarise(run_local(rows), rows)
        (HERE / "result.json").write_text(json.dumps({"engine": "pure-Python classical decomposition (local) / "
                                                      "BigQuery ML.SEASONALITY (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
