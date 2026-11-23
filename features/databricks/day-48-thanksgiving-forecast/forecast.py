"""Day 48 (Databricks): Thanksgiving-week demand for three seasonal SKUs with **ai_forecast version 2** (GA in the
Databricks platform release notes of 2026-11-18): a research-optimized time series foundation model with built-in
holidays (`holiday_region`), external covariates (`covariate_col`) and non-negative forecasts (`positive_only`),
selected with `version => '2'`.

One store's daily units for whole turkeys, cranberry sauce and pumpkin pie run from 2024-10-01 to 2026-11-14, with
Thanksgiving spikes in 2024 and 2025, a weekly pattern and promo days. The store plans a promo for 2026-11-20..25.
Three calls forecast 2026-11-15..30 (Thanksgiving is 11-26):

  plain     version 2, history only
  holiday   + holiday_region => 'US', positive_only => true
  promo     + covariate_col => 'promo' (future rows carry the planned promo and NULL units)

The data come from a deterministic generator, so the held-out "actual" days are known and each variant gets a
MAPE and a Thanksgiving-week error next to two local baselines (same weekday last week; same days around last
Thanksgiving, scaled). Model outputs aren't reproducible locally, so the cloud check is structural plus reported
accuracy.

  python forecast.py                 # local: data, baselines, writes result.json
  python forecast.py --sql VARIANT   # print the Databricks SQL for one variant
  python forecast.py --databricks    # serverless SQL warehouse via the Statement Execution API (cloud.sh)

Must hold (else the run fails): each variant returns 16 days x 3 SKUs with units_forecast / units_lower /
units_upper and lower <= forecast <= upper; with positive_only every forecast and lower bound is >= 0.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
START, LAST_OBS, HORIZON = date(2024, 10, 1), date(2026, 11, 14), date(2026, 12, 1)   # horizon is exclusive
THANKSGIVING = {2024: date(2024, 11, 28), 2025: date(2025, 11, 27), 2026: date(2026, 11, 26)}
PLANNED_PROMO = (date(2026, 11, 20), date(2026, 11, 25))
SKUS = {  # base units/day, Thanksgiving multiplier on the peak day, promo lift
    "TURKEY-WHOLE": (2.0, 30.0, 0.35),
    "CRANBERRY-SAUCE": (6.0, 8.0, 0.25),
    "PUMPKIN-PIE": (4.0, 10.0, 0.30),
}
# share of the peak-day lift from 7 days before Thanksgiving to 2 days after (day 0 = Wednesday before is -1)
RAMP = {-7: 0.08, -6: 0.12, -5: 0.18, -4: 0.28, -3: 0.45, -2: 0.7, -1: 1.0, 0: 0.15, 1: 0.05, 2: 0.02}
WEEKDAY = [0.9, 0.85, 0.9, 1.0, 1.15, 1.35, 1.1]   # Mon..Sun
VARIANTS = ("plain", "holiday", "promo")


def promo_on(d: date) -> bool:
    if PLANNED_PROMO[0] <= d <= PLANNED_PROMO[1]:
        return True
    # history: a 4-day promo every 5 weeks, never near Thanksgiving
    return d <= LAST_OBS and (d - START).days % 35 < 4 and not any(abs((d - t).days) < 14 for t in THANKSGIVING.values())


def units(sku: str, d: date) -> int:
    base, peak, lift = SKUS[sku]
    v = base * WEEKDAY[d.weekday()]
    tg = THANKSGIVING.get(d.year)
    if tg is not None and (d - tg).days in RAMP:
        v += base * (peak - 1) * RAMP[(d - tg).days]
    if promo_on(d):
        v *= 1 + lift
    i = (d - START).days
    noise = ((i * 37 + len(sku) * 11) % 7 - 3) * 0.08 * base     # deterministic, mean zero over a week
    return max(0, round(v + noise))


def days(a: date, b: date):   # [a, b)
    return [a + timedelta(n) for n in range((b - a).days)]


def history() -> list[tuple[str, str, int, int]]:
    return [(d.isoformat(), s, units(s, d), int(promo_on(d))) for s in SKUS for d in days(START, LAST_OBS + timedelta(1))]


def future_dates() -> list[date]:
    return days(LAST_OBS + timedelta(1), HORIZON)


def actuals() -> dict[tuple[str, str], int]:
    return {(d.isoformat(), s): units(s, d) for s in SKUS for d in future_dates()}


# ---------- local baselines
def baseline_last_week() -> dict[tuple[str, str], float]:
    """Same weekday in the last observed week, repeated."""
    obs = {(d, s): u for d, s, u, _ in history()}
    out = {}
    for s in SKUS:
        for d in future_dates():
            back = d - timedelta(7 * math.ceil((d - LAST_OBS).days / 7))
            out[(d.isoformat(), s)] = obs[(back.isoformat(), s)]
    return out


def baseline_last_thanksgiving() -> dict[tuple[str, str], float]:
    """Same offset from last year's Thanksgiving, scaled by the ratio of the last 4 weeks to the same 4 weeks a year
    earlier (aligned to Thanksgiving)."""
    obs = {(d, s): u for d, s, u, _ in history()}
    shift = THANKSGIVING[2026] - THANKSGIVING[2025]
    out = {}
    for s in SKUS:
        recent = sum(obs[((LAST_OBS - timedelta(k)).isoformat(), s)] for k in range(28))
        before = sum(obs[((LAST_OBS - timedelta(k) - shift).isoformat(), s)] for k in range(28))
        scale = recent / before if before else 1.0
        for d in future_dates():
            out[(d.isoformat(), s)] = obs[((d - shift).isoformat(), s)] * scale
    return out


def errors(pred: dict, act: dict) -> dict:
    """WAPE over the 16 days (sum |error| / sum actual, robust to zero days) and over Thanksgiving week (11-19..26)."""
    tg = THANKSGIVING[2026]
    week = {k for k in act if tg - timedelta(7) <= date.fromisoformat(k[0]) <= tg}
    def wape(keys):
        den = sum(act[k] for k in keys)
        return round(sum(abs(pred[k] - act[k]) for k in keys) / den, 3) if den else None
    per_sku = {s: wape([k for k in act if k[1] == s]) for s in SKUS}
    return {"wape": wape(list(act)), "wape_thanksgiving_week": wape(week), "per_sku": per_sku}


# ---------- SQL
def _values(rows) -> str:
    return ",\n    ".join(f"(DATE'{d}', '{s}', {'NULL' if u is None else u}, {p})" for d, s, u, p in rows)


def sql(variant: str) -> str:
    rows = list(history())
    if variant == "promo":
        rows += [(d.isoformat(), s, None, int(promo_on(d))) for s in SKUS for d in future_dates()]
    extra = {"plain": "",
             "holiday": ",\n  holiday_region => 'US',\n  positive_only => true",
             "promo": ",\n  holiday_region => 'US',\n  positive_only => true,\n  covariate_col => 'promo'"}[variant]
    return f"""-- Day 48 ({variant}): Thanksgiving-week demand with ai_forecast version 2 (GA 2026-11-18)
WITH daily AS (
  SELECT ds, sku, CAST(units AS DOUBLE) AS units, CAST(promo AS DOUBLE) AS promo
  FROM VALUES
    {_values(rows)}
  AS t(ds, sku, units, promo)
)
SELECT '{variant}' AS variant, CAST(ds AS STRING) AS ds, sku, units_forecast, units_lower, units_upper
FROM ai_forecast(
  TABLE(daily),
  horizon => '{HORIZON.isoformat()}',
  time_col => 'ds',
  value_col => 'units',
  group_col => 'sku',
  frequency => 'D'{extra},
  version => '2'
)
ORDER BY sku, ds"""


# ---------- cloud output check
def parse(rows: list[list]) -> dict[tuple[str, str], dict]:
    """Statement Execution API JSON_ARRAY rows: variant, ds, sku, forecast, lower, upper (strings or null)."""
    out = {}
    for r in rows:
        f = lambda v: None if v is None else float(v)  # noqa: E731
        out[(str(r[1])[:10], str(r[2]))] = {"forecast": f(r[3]), "lower": f(r[4]), "upper": f(r[5])}
    return out


def check(variant: str, got: dict) -> list[str]:
    problems = []
    want = set(actuals())
    if set(got) != want:
        problems.append(f"{variant}: {len(got)} rows, expected {len(want)} (16 days x 3 SKUs); "
                        f"missing {sorted(want - set(got))[:3]}")
    for k, v in got.items():
        if None in v.values():
            problems.append(f"{variant}: NULL in {k}")
            continue
        if not v["lower"] - 1e-6 <= v["forecast"] <= v["upper"] + 1e-6:
            problems.append(f"{variant}: interval out of order at {k}: {v}")
        if variant != "plain" and (v["forecast"] < -1e-9 or v["lower"] < -1e-9):
            problems.append(f"{variant}: positive_only but negative at {k}: {v}")
    return problems[:6]


def score(got: dict) -> dict:
    act = actuals()
    pred = {k: got[k]["forecast"] for k in act if k in got}
    if len(pred) != len(act):
        return {}
    e = errors(pred, act)
    tg = THANKSGIVING[2026]
    e["turkey_peak_forecast"] = round(pred[((tg - timedelta(1)).isoformat(), "TURKEY-WHOLE")], 1)
    e["negative_forecasts"] = sum(v < 0 for v in pred.values())
    return e


# ---------- Databricks SQL (Statement Execution API)
def _redact(text: str) -> str:
    host = os.environ.get("DATABRICKS_HOST", "").removeprefix("https://").rstrip("/")
    if host:
        text = text.replace(host, "<host>")
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<user>", text)
    return re.sub(r"\b\d{7,}\b", "<id>", text)


class Refused(Exception):
    pass


def _api(method: str, path: str, body: dict | None = None) -> dict:
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    if not host.startswith("https://"):
        host = "https://" + host
    req = urllib.request.Request(host + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise Refused(f"{method} {path.split('?')[0]} {e.code}: {e.read()[:300].decode(errors='replace')}")


def statement(wh: str, text: str) -> list[list]:
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": text, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(84):  # up to ~7 more minutes: warehouse start plus model inference
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")
        # errors come through the Python UDTF engine: the real message is at the end of the traceback
        raise RuntimeError(f"{res['status']['state']}: ...{msg[-450:]}".replace("\n", " "))
    return res.get("result", {}).get("data_array", []) or []


def compute_refused(text: str) -> bool:
    t = text.lower()
    return "feature_disabled" in t or "not active" in t or "cancelled" in t or "could not be processed by the warehouse" in t


def run_databricks() -> int:
    try:
        whs = _api("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
        if not whs:
            print("::error title=Databricks day 48::no SQL warehouse in the workspace")
            return 1
        wh = next((w for w in whs if w.get("enable_serverless_compute")), whs[0])["id"]
    except Refused as e:
        if compute_refused(str(e)):
            print(f"::warning title=Databricks day 48::the workspace refused ({_redact(str(e))[:300]}); the account "
                  "looks inactive, so the forecast data and baselines were checked locally only")
            return 0
        print(f"::error title=Databricks day 48 failed::{_redact(str(e))[:600]}")
        return 1
    failed, scores = 0, {}
    for v in VARIANTS:
        try:
            got = parse(statement(wh, sql(v)))
        except (Refused, RuntimeError) as e:
            if compute_refused(str(e)):
                print(f"::warning title=Databricks day 48::the warehouse refused ({_redact(str(e))[:300]}); "
                      "checked locally only")
                return 0
            print(f"::error title=Databricks day 48 {v} failed::{_redact(str(e))[:700]}")
            failed = 1
            continue
        problems = check(v, got)
        if problems:
            print(f"::error title=Databricks day 48 {v} check failed::{_redact('; '.join(problems))[:800]}")
            failed = 1
            continue
        scores[v] = score(got)
    if scores:
        local = summarise()["baselines"]
        parts = [f"{v}: WAPE {s['wape']} (Thanksgiving week {s['wape_thanksgiving_week']}), turkey on 11-25 "
                 f"{s['turkey_peak_forecast']}, {s['negative_forecasts']} negative" for v, s in scores.items()]
        print(f"::notice title=Databricks day 48::ai_forecast version 2, 16 days x 3 SKUs, actual turkeys on 11-25: "
              f"{local['actual_turkey_peak']}. " + "; ".join(parts) + f". Baselines: last week WAPE "
              f"{local['last_week']['wape']}, last Thanksgiving WAPE {local['last_thanksgiving']['wape']}.")
        print("DAY48_SCORES " + json.dumps(scores))
    return failed


def summarise() -> dict:
    h, act = history(), actuals()
    tg = THANKSGIVING[2026]
    return {
        "history_rows": len(h), "skus": len(SKUS), "history_days": len(h) // len(SKUS),
        "forecast_days": len(future_dates()), "horizon_rows": len(act),
        "thanksgivings_in_history": 2, "planned_promo_days": (PLANNED_PROMO[1] - PLANNED_PROMO[0]).days + 1,
        "variants": list(VARIANTS),
        "baselines": {
            "actual_turkey_peak": act[((tg - timedelta(1)).isoformat(), "TURKEY-WHOLE")],
            "actual_units_horizon": sum(act.values()),
            "last_week": errors(baseline_last_week(), act),
            "last_thanksgiving": errors(baseline_last_thanksgiving(), act),
        },
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql(sys.argv[2]))
    elif mode == "--databricks":
        raise SystemExit(run_databricks())
    else:
        s = summarise()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "deterministic generator + local baselines (local) / ai_forecast version 2 on a serverless "
                       "SQL warehouse (cloud)", **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
