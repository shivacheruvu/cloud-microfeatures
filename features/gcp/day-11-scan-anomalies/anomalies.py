"""Day 11 (Google Cloud): flag unusual days in the daily scan counts of three grocery staples with BigQuery's
AI.DETECT_ANOMALIES and TimesFM 3.0 (Preview 2026-10-07: TimesFM 3.0 now also does anomaly detection).

  python anomalies.py --make-data            # regenerate data/scans.csv (deterministic)
  python anomalies.py --local                # pure Python weekday-median baseline on the target days; writes result.json
  python anomalies.py --sql [MODEL]          # print the BigQuery query (default model 'TimesFM 3.0')
  python anomalies.py --compare OUT [MODEL]  # check bq --format=json output against the injected anomalies

Three planted problems in the 4 target weeks: a banana scanner outage (units near zero), a duplicate-scan spike on
milk (2.5x), and a bread delivery short (0.4x). The cloud check:
  * errors:   a target point missing, with NULL is_anomaly or a non-empty ai_detect_anomalies_status; fewer than 2 of
              the 3 planted anomalies flagged
  * warnings: one planted anomaly missed, or more than 4 of the 81 normal days flagged
TimesFM isn't deterministic across versions, so the check compares what's flagged, not exact bounds.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "scans.csv"
START = date(2026, 7, 20)                # a Monday
HISTORY_DAYS, TARGET_DAYS = 56, 28       # 8 weeks of history, 4 weeks checked
PRODUCTS = {                              # Mon..Sun daily units at one store
    "bananas (lb)": [180, 170, 175, 185, 210, 260, 230],
    "milk 1 gal": [120, 115, 118, 122, 140, 165, 150],
    "sandwich bread": [80, 76, 78, 82, 95, 110, 100],
}
PLANTED = {  # target-day offset -> (product, multiplier, what happened)
    6: ("bananas (lb)", 0.03, "scanner outage"),
    15: ("milk 1 gal", 2.5, "duplicate scans"),
    23: ("sandwich bread", 0.4, "short delivery"),
}
MODEL = "TimesFM 3.0"
THRESHOLD = 0.95          # the function's default, written out
LOCAL_BAND = 0.30         # local baseline flags a day more than 30% away from its weekday median
MIN_CAUGHT, MAX_FALSE = 2, 4


def make_data() -> list[dict]:
    seed, rows = 20261015, []
    for name, base in PRODUCTS.items():
        for i in range(HISTORY_DAYS + TARGET_DAYS):
            seed = (seed * 1103515245 + 12345) % 2**31          # LCG: same numbers everywhere
            noise = 1 + ((seed / 2**31) - 0.5) * 0.12            # +-6%
            d = START + timedelta(days=i)
            t = i - HISTORY_DAYS
            mult, why = (PLANTED[t][1], PLANTED[t][2]) if t in PLANTED and PLANTED[t][0] == name else (1, "")
            rows.append({"product": name, "day": d.isoformat(),
                         "units": round(base[d.weekday()] * (1 + 0.001 * i) * noise * mult),
                         "role": "history" if i < HISTORY_DAYS else "target", "planted": why})
    return rows


def load_rows() -> list[dict]:
    with open(DATA, newline="") as f:
        return [{**r, "units": int(r["units"])} for r in csv.DictReader(f)]


def split(rows):
    return [r for r in rows if r["role"] == "history"], [r for r in rows if r["role"] == "target"]


# ---------- local baseline
def run_local(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Weekday median of the last 4 history weeks per product; flag target days more than LOCAL_BAND away."""
    hist, target = split(rows)
    med: dict[tuple[str, int], float] = {}
    for p in PRODUCTS:
        last = [r for r in hist if r["product"] == p][-28:]
        for wd in range(7):
            med[(p, wd)] = median(r["units"] for r in last if date.fromisoformat(r["day"]).weekday() == wd)
    out = {}
    for r in target:
        exp = med[(r["product"], date.fromisoformat(r["day"]).weekday())]
        dev = r["units"] / exp - 1
        out[(r["product"], r["day"])] = {"expected": exp, "deviation": round(dev, 3), "is_anomaly": abs(dev) > LOCAL_BAND}
    return out


def score(flags: dict[tuple[str, str], bool], rows: list[dict]) -> dict:
    _, target = split(rows)
    planted = [(r["product"], r["day"]) for r in target if r["planted"]]
    normal = [(r["product"], r["day"]) for r in target if not r["planted"]]
    return {"caught": [k for k in planted if flags.get(k)], "missed": [k for k in planted if not flags.get(k)],
            "false_alarms": [k for k in normal if flags.get(k)]}


# ---------- BigQuery
def _struct(r: dict) -> str:
    return f"STRUCT('{r['product']}' AS product, DATE '{r['day']}' AS day, {float(r['units'])} AS units)"


def bigquery_sql(rows: list[dict], model: str = MODEL) -> str:
    hist, target = split(rows)
    return ((HERE / "anomalies.sql").read_text()
            .replace("{history}", ",\n    ".join(_struct(r) for r in hist))
            .replace("{target}", ",\n    ".join(_struct(r) for r in target))
            .replace("{model}", model).replace("{threshold}", str(THRESHOLD)))


def read_bq(path: str) -> list[dict]:
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a warning line before the JSON
    try:
        return json.loads(text[start:]) if start >= 0 else []
    except Exception as e:  # noqa: BLE001 - any shape surprise is reported, redacted, as an annotation
        raw = text[max(start, 0):max(start, 0) + 500].replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {e}; {raw!r}")
        raise SystemExit(1)


def _day(v) -> str | None:
    if v is None:
        return None
    try:  # bq json may give epoch seconds for TIMESTAMP
        return datetime.fromtimestamp(float(v), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return str(v)[:10]


def _bool(v) -> bool | None:
    if isinstance(v, bool) or v is None:
        return v
    return {"true": True, "false": False}.get(str(v).lower())


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def cloud_points(cloud: list[dict]) -> dict[tuple[str, str], dict]:
    out = {}
    for c in cloud:
        k = (c.get("product"), _day(c.get("time_series_timestamp", c.get("day"))))
        out[k] = {"is_anomaly": _bool(c.get("is_anomaly")), "status": c.get("ai_detect_anomalies_status") or "",
                  "lower": _num(c.get("lower_bound")), "upper": _num(c.get("upper_bound")),
                  "prob": _num(c.get("anomaly_probability")), "value": _num(c.get("time_series_data"))}
    return out


def compare(cloud: list[dict], rows: list[dict]):
    _, target = split(rows)
    pts = cloud_points(cloud)
    errors, warnings = [], []
    for r in target:
        p = pts.get((r["product"], r["day"]))
        if p is None:
            errors.append(f"{r['product']} {r['day']}: no output row")
        elif p["is_anomaly"] is None or p["status"]:
            errors.append(f"{r['product']} {r['day']}: is_anomaly {p['is_anomaly']}, status {p['status'][:100]!r}")
    if errors:
        return errors[:8], warnings, {}
    s = score({k: v["is_anomaly"] for k, v in pts.items()}, rows)
    if len(s["caught"]) < MIN_CAUGHT:
        errors.append(f"only {len(s['caught'])} of {len(PLANTED)} planted anomalies flagged; missed {s['missed']}")
    elif s["missed"]:
        warnings.append(f"missed planted anomaly {s['missed']}")
    if len(s["false_alarms"]) > MAX_FALSE:
        warnings.append(f"{len(s['false_alarms'])} normal days flagged: {s['false_alarms'][:8]}")
    planted = {}
    for r in target:
        if r["planted"]:
            p = pts[(r["product"], r["day"])]
            planted[f"{r['product']} {r['day']} ({r['planted']})"] = {
                "units": r["units"], "flagged": p["is_anomaly"], "bounds": [round(p["lower"] or 0), round(p["upper"] or 0)],
                "prob": None if p["prob"] is None else round(p["prob"], 3)}
    summary = {"caught": len(s["caught"]), "false_alarms": len(s["false_alarms"]),
               "false_alarm_days": [f"{a} {b}" for a, b in s["false_alarms"]], "planted": planted}
    return errors, warnings, summary


def summarise(rows: list[dict], local: dict) -> dict:
    hist, target = split(rows)
    s = score({k: v["is_anomaly"] for k, v in local.items()}, rows)
    return {
        "products": len(PRODUCTS), "history_days": HISTORY_DAYS, "target_days": TARGET_DAYS,
        "target_points": len(target), "planted_anomalies": len(PLANTED), "model": MODEL,
        "anomaly_prob_threshold": THRESHOLD,
        "local_baseline": f"weekday median of the last 4 history weeks, flag beyond +-{int(LOCAL_BAND * 100)}%",
        "local_caught": len(s["caught"]), "local_false_alarms": len(s["false_alarms"]),
        "table": [{"product": r["product"], "day": r["day"], "weekday": date.fromisoformat(r["day"]).strftime("%a"),
                   "what": r["planted"], "units": r["units"],
                   "weekday_median": local[(r["product"], r["day"])]["expected"],
                   "deviation_pct": round(100 * local[(r["product"], r["day"])]["deviation"], 1),
                   "local_flag": local[(r["product"], r["day"])]["is_anomaly"]} for r in target if r["planted"]],
        "headline": f"{len(target)} store-days checked across {len(PRODUCTS)} staples, {len(PLANTED)} planted problems: "
                    f"local baseline catches {len(s['caught'])} with {len(s['false_alarms'])} false alarms; "
                    "TimesFM 3.0 must catch at least 2",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        with open(DATA, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["product", "day", "units", "role", "planted"])
            w.writeheader()
            w.writerows(make_data())
        raise SystemExit(0)
    rows = load_rows()
    if mode == "--sql":
        print(bigquery_sql(rows, sys.argv[2] if len(sys.argv) > 2 else MODEL))
    elif mode == "--compare":
        cloud = read_bq(sys.argv[2])
        model = sys.argv[3] if len(sys.argv) > 3 else MODEL
        try:
            errors, warnings, summary = compare(cloud, rows)
        except Exception as e:  # report what BigQuery returned (data rows only: no project or account details)
            print(f"::error title=AI.DETECT_ANOMALIES output not understood::{type(e).__name__}: {e}; "
                  f"{len(cloud)} row(s), first: {json.dumps(cloud[:2])[:700]}")
            raise SystemExit(1)
        for w in warnings:
            print(f"::warning title=AI.DETECT_ANOMALIES ({model}) vs planted::{w[:900]}")
        if errors:
            print(f"::error title=AI.DETECT_ANOMALIES ({model}) check failed::{json.dumps(errors)[:800]}; "
                  f"first row {json.dumps(cloud[:1])[:400]}")
            raise SystemExit(1)
        s = summarise(rows, run_local(rows))
        print(f"::notice title=Google Cloud day 11::AI.DETECT_ANOMALIES with {model} ran on BigQuery over "
              f"{s['target_points']} store-days (3 staples): caught {summary['caught']} of {len(PLANTED)} planted "
              f"anomalies, {summary['false_alarms']} false alarms {json.dumps(summary['false_alarm_days'])[:200]}; "
              f"local weekday-median baseline caught {s['local_caught']} with {s['local_false_alarms']} false alarms; "
              f"planted {json.dumps(summary['planted'])[:500]}")
    else:
        local = run_local(rows)
        summary = summarise(rows, local)
        (HERE / "result.json").write_text(json.dumps({"engine": "pure-Python weekday-median baseline (local) / "
                                                      "BigQuery AI.DETECT_ANOMALIES with TimesFM 3.0 (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
