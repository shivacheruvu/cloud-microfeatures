"""Day 4 (Google Cloud): when did a store's oat-milk sales shift? BigQuery's ML.DETECT_CHANGE_POINTS (Preview).

  python change_points.py --local          # local mean-shift detector (pure Python); writes result.json
  python change_points.py --sql            # prints the BigQuery query (data inlined, nothing to load or clean up)
  python change_points.py --compare FILE   # checks BigQuery's JSON output against the local result

BigQuery's algorithm isn't published, so the cloud check is:
  * errors:   any non-empty status; no BigQuery change window for east that contains the local change date
  * exact:    each window's metrics (count, min, max, avg; stddev sample or population) recomputed locally over
              the window's own dates; a mismatch is a warning that records how BigQuery defines them
  * reported: windows for west (a flat series) are reported as a warning, not an error
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_SHIFT_SIGMAS = 3.0   # local detector: the mean shift must be at least this many within-segment std devs
MIN_SEGMENT = 7          # and each side at least a week long


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
    return (HERE / "change_points.sql").read_text().replace("{values}", ",\n".join(structs))


def detect(points: list[tuple[str, int]]) -> dict | None:
    """Best single mean shift (least squares split). Returns None unless it is large and sustained."""
    ys = [y for _, y in points]
    best = None
    for k in range(MIN_SEGMENT, len(ys) - MIN_SEGMENT + 1):
        a, b = ys[:k], ys[k:]
        sse = sum((y - statistics.fmean(a)) ** 2 for y in a) + sum((y - statistics.fmean(b)) ** 2 for y in b)
        if best is None or sse < best[0]:
            best = (sse, k)
    sse, k = best
    a, b = ys[:k], ys[k:]
    sigma = math.sqrt(sse / (len(ys) - 2))
    shift = statistics.fmean(b) - statistics.fmean(a)
    if abs(shift) < MIN_SHIFT_SIGMAS * sigma:
        return None
    return {"change_date": points[k][0], "avg_before": round(statistics.fmean(a), 1),
            "avg_after": round(statistics.fmean(b), 1), "shift_pct": round(100 * shift / statistics.fmean(a), 1)}


def run_local(rows: list[dict]) -> dict[str, dict | None]:
    return {store: detect(pts) for store, pts in series(rows).items()}


def window_metrics(points: list[tuple[str, int]], begin: str, end: str) -> dict:
    ys = [y for d, y in points if begin <= d <= end]
    return {"count": len(ys), "min": min(ys), "max": max(ys), "avg": statistics.fmean(ys),
            "stddev_samp": statistics.stdev(ys) if len(ys) > 1 else 0.0, "stddev_pop": statistics.pstdev(ys)}


def read_bq(path: str) -> list[dict]:
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a warning line before the JSON
    try:
        rows = json.loads(text[start:]) if start >= 0 else []
        out = []
        for r in rows:
            m = r.get("metrics") or {}
            out.append({"store": r["store"], "begin": str(r.get("begin_timestamp") or "")[:10],
                        "end": str(r.get("end_timestamp") or "")[:10], "status": r.get("status") or "",
                        "metrics": {k: (None if m.get(k) is None else float(m[k]))
                                    for k in ("count", "min", "max", "avg", "stddev")}})
        return out
    except (ValueError, KeyError, TypeError) as e:
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {text[:300]!r}")
        raise SystemExit(1)


def compare(cloud: list[dict], rows: list[dict]) -> tuple[list[str], list[str], dict]:
    """Returns (errors, warnings, summary) for BigQuery's change windows against the local data and detector."""
    local, ser = run_local(rows), series(rows)
    errors, warnings, matched = [], [], 0
    bad_status = [w for w in cloud if w["status"]]
    if bad_status:
        errors.append(f"status: {bad_status[0]['status'][:200]}")
    for w in cloud:
        if w["status"] or w["store"] not in ser:
            continue
        exp, got = window_metrics(ser[w["store"]], w["begin"], w["end"]), w["metrics"]
        ok = (got["count"] == exp["count"] and got["min"] == exp["min"] and got["max"] == exp["max"]
              and math.isclose(got["avg"] or 0, exp["avg"], rel_tol=1e-6)
              and any(math.isclose(got["stddev"] or 0, exp[k], rel_tol=1e-6, abs_tol=1e-9)
                      for k in ("stddev_samp", "stddev_pop")))
        if ok:
            matched += 1
        else:
            warnings.append(f"metrics differ for {w['store']} {w['begin']}..{w['end']}: bq={got} local={exp}")
    for store, found in local.items():
        wins = [w for w in cloud if w["store"] == store and not w["status"]]
        if found and not any(w["begin"] <= found["change_date"] <= w["end"] for w in wins):
            errors.append(f"{store}: no BigQuery window contains the local change date {found['change_date']} "
                          f"(windows: {[(w['begin'], w['end']) for w in wins][:5]})")
        if not found and wins:
            warnings.append(f"{store}: BigQuery reports {len(wins)} window(s) on a series with no local shift: "
                            f"{[(w['begin'], w['end']) for w in wins][:5]}")
    summary = {"windows": len(cloud), "metrics_matched": matched,
               "by_store": {s: [(w["begin"], w["end"]) for w in cloud if w["store"] == s] for s in ser}}
    return errors, warnings, summary


def summarise(local: dict[str, dict | None], rows: list[dict]) -> dict:
    ser = series(rows)
    return {"days": len(next(iter(ser.values()))), "stores": len(ser),
            "shifts": {s: v for s, v in local.items()},
            "headline": "; ".join(f"{s}: {v['shift_pct']:+.0f}% from {v['change_date']}" if v else f"{s}: no shift"
                                  for s, v in local.items())}


if __name__ == "__main__":
    rows = load_rows()
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(bigquery_sql(rows))
    elif mode == "--compare":
        errors, warnings, summary = compare(read_bq(sys.argv[2]), rows)
        for w in warnings:
            print(f"::warning title=ML.DETECT_CHANGE_POINTS vs local::{w[:900]}")
        if errors:
            print(f"::error title=ML.DETECT_CHANGE_POINTS differs from local::{json.dumps(errors)[:900]}")
            raise SystemExit(1)
        print(f"::notice title=Google Cloud day 4::ML.DETECT_CHANGE_POINTS ran on BigQuery and agreed with local "
              f"({json.dumps(summarise(run_local(rows), rows)['headline'])}): {json.dumps(summary)[:700]}")
    else:
        summary = summarise(run_local(rows), rows)
        (HERE / "result.json").write_text(json.dumps({"engine": "pure-Python mean-shift detector (local) / BigQuery "
                                                      "ML.DETECT_CHANGE_POINTS (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
