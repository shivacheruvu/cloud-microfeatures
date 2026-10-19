"""Day 14 (Google Cloud): what drives ice cream sales? ML.CORRELATION (BigQuery ML, Preview 2026-09-10) correlates
daily ice cream revenue with the day's high temperature, the promotion depth and foot traffic, per store and overall,
with Pearson and Spearman, in one SQL call each and no model. Finding from the first cloud run: the SPEARMAN rollup
row correlates ranks taken within each store, not ranks over all rows; a call without dimension_cols gives the latter.

  python drivers.py --make-data    # regenerate data/days.csv (deterministic, no ties in any column)
  python drivers.py --local        # pure-Python Pearson and Spearman; writes result.json
  python drivers.py --sql          # print the BigQuery statement (data inlined)
  python drivers.py --compare OUT  # check bq's JSON output against the local correlations

The cloud check (cloud.sh, GitHub Actions):
  * errors: a missing or extra (method, store, column) row, a segment size that differs, or a correlation more than
    1e-6 away from the local value
Stores and sales are made up.
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DAYS_CSV = HERE / "data" / "days.csv"
STORES = {"S1": "Lakeside", "S2": "Downtown", "S3": "Campus"}
COLS = ("max_temp_c", "promo_pct", "foot_traffic")
METHODS = ("PEARSON", "SPEARMAN")
N_DAYS = 28
TOLERANCE = 1e-6


def make_data() -> list[dict]:
    """28 days of September 2026 per store. Lakeside sells on hot days, Downtown on promotion days, Campus with foot
    traffic; each column is perturbed so no value repeats (rank correlations then need no tie rule)."""
    rng = random.Random(1914)
    rows, seen = [], {c: set() for c in ("ice_cream_revenue",) + COLS}

    def unique(col: str, v: float, step: float) -> float:
        while v in seen[col]:
            v = round(v + step, 2)
        seen[col].add(v)
        return v

    for store in STORES:
        for d in range(N_DAYS):
            day = date(2026, 9, 1) + timedelta(d)
            temp = round(17 + 9 * math.sin(d / 4.5) + rng.uniform(-3, 3) - d * 0.12, 2)
            promo = round(rng.choice((0, 0, 0, 10, 15, 25)) + rng.uniform(0, 0.99), 2)
            traffic = round(1300 + 250 * (day.weekday() >= 5) + rng.uniform(-300, 300), 0)
            if store == "S1":
                rev = 40 + 9.5 * temp + 1.2 * promo + 0.01 * traffic + rng.uniform(-25, 25)
            elif store == "S2":
                rev = 120 + 1.0 * temp + 8.0 * promo + 0.02 * traffic + rng.uniform(-30, 30)
            else:
                rev = -40 + 1.5 * temp + 0.5 * promo + 0.16 * traffic + rng.uniform(-30, 30)
            rows.append({"store": store, "sale_date": day.isoformat(),
                         "ice_cream_revenue": f"{unique('ice_cream_revenue', round(rev, 2), 0.01):.2f}",
                         "max_temp_c": f"{unique('max_temp_c', temp, 0.01):.2f}",
                         "promo_pct": f"{unique('promo_pct', promo, 0.01):.2f}",
                         "foot_traffic": str(int(unique('foot_traffic', traffic, 1)))})
    return rows


def load() -> list[dict]:
    with open(DAYS_CSV, newline="") as f:
        return list(csv.DictReader(f))


def bigquery_sql(days: list[dict]) -> str:
    vs = ",\n    ".join(f"STRUCT('{r['store']}' AS store, DATE '{r['sale_date']}' AS sale_date, "
                        f"{r['ice_cream_revenue']} AS ice_cream_revenue, {r['max_temp_c']} AS max_temp_c, "
                        f"{r['promo_pct']} AS promo_pct, {int(r['foot_traffic'])} AS foot_traffic)" for r in days)
    return (HERE / "correlation.sql").read_text().replace("{days}", vs)


# ---------- local: Pearson and Spearman in pure Python
def pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy)


def ranks(x: list[float]) -> list[float]:
    """1-based ranks, ties get the average rank (the data has none, so any tie rule gives the same answer)."""
    order = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x: list[float], y: list[float]) -> float:
    return pearson(ranks(x), ranks(y))


def run_local(days: list[dict]) -> dict[tuple[str, str, str], dict]:
    """The rows BigQuery returns. Per store: plain Pearson / Spearman. The overall (rollup) row: Pearson as usual,
    but BigQuery's SPEARMAN rollup ranks within each store and then correlates the pooled ranks (seen on the first
    cloud run), so that is modelled here; SPEARMAN_NO_DIMS (a call without dimension_cols) is the all-rows Spearman."""
    segments = {s: [r for r in days if r["store"] == s] for s in STORES}
    y = lambda rows: [float(r["ice_cream_revenue"]) for r in rows]  # noqa: E731
    x = lambda rows, col: [float(r[col]) for r in rows]  # noqa: E731
    out = {}
    for col in COLS:
        for seg, rows in segments.items():
            out[("PEARSON", seg, col)] = {"correlation": pearson(x(rows, col), y(rows)), "segment_size": len(rows)}
            out[("SPEARMAN", seg, col)] = {"correlation": spearman(x(rows, col), y(rows)), "segment_size": len(rows)}
        out[("PEARSON", "ALL", col)] = {"correlation": pearson(x(days, col), y(days)), "segment_size": len(days)}
        rx = [v for rows in segments.values() for v in ranks(x(rows, col))]
        ry = [v for rows in segments.values() for v in ranks(y(rows))]
        out[("SPEARMAN", "ALL", col)] = {"correlation": pearson(rx, ry), "segment_size": len(days)}
        out[("SPEARMAN_NO_DIMS", "ALL", col)] = {"correlation": spearman(x(days, col), y(days)),
                                                 "segment_size": len(days)}
    return out


# ---------- BigQuery output
def read_bq(path: str) -> list[dict]:
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a warning line before the JSON
    try:
        return json.loads(text[start:]) if start >= 0 else []
    except Exception as e:  # noqa: BLE001 - any shape surprise is reported, redacted, as an annotation
        raw = text[max(start, 0):max(start, 0) + 500].replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {e}; {raw!r}")
        raise SystemExit(1)


def compare(cloud: list[dict], local: dict[tuple[str, str, str], dict]) -> list[str]:
    got: dict[tuple, dict] = {}
    errors = []
    for c in cloud:
        k = (c.get("method"), c.get("store"), c.get("corr_col"))
        if k in got:
            errors.append(f"{'/'.join(map(str, k))}: duplicate row")
        got[k] = c
    errors += [f"{'/'.join(k)}: no output row" for k in local if k not in got]
    errors += [f"{'/'.join(map(str, k))}: unexpected row" for k in got if k not in local]
    for k, want in local.items():
        if k not in got:
            continue
        try:
            corr, size = float(got[k].get("correlation")), int(got[k].get("segment_size"))
        except (TypeError, ValueError):
            errors.append(f"{'/'.join(k)}: unreadable {got[k]}")
            continue
        if size != want["segment_size"]:
            errors.append(f"{'/'.join(k)}: segment_size {size} vs local {want['segment_size']}")
        if not math.isfinite(corr) or abs(corr - want["correlation"]) > TOLERANCE:
            errors.append(f"{'/'.join(k)}: BigQuery {corr!r} vs local {want['correlation']:.6f}")
    return errors


def summarise(local: dict[tuple[str, str, str], dict]) -> dict:
    table = {m: {s: {c: round(local[(m, s, c)]["correlation"], 3) for c in COLS} for s in ("ALL", *STORES)}
             for m in METHODS}
    table["SPEARMAN"]["ALL_NO_DIMS"] = {c: round(local[("SPEARMAN_NO_DIMS", "ALL", c)]["correlation"], 3)
                                        for c in COLS}
    driver = {s: max(COLS, key=lambda c: table["PEARSON"][s][c]) for s in ("ALL", *STORES)}
    p = table["PEARSON"]
    return {
        "days": N_DAYS, "stores": len(STORES), "rows": N_DAYS * len(STORES),
        "correlation": table, "top_driver": driver,
        "headline": f"Lakeside follows the weather (r={p['S1']['max_temp_c']}), Downtown the promotion "
                    f"(r={p['S2']['promo_pct']}), Campus foot traffic (r={p['S3']['foot_traffic']}); pooled, temperature "
                    f"shows only r={p['ALL']['max_temp_c']}; the SPEARMAN rollup row ranks within stores "
                    f"({table['SPEARMAN']['ALL']['foot_traffic']} for foot traffic vs "
                    f"{table['SPEARMAN']['ALL_NO_DIMS']['foot_traffic']} over all rows)",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        rows = make_data()
        with open(DAYS_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        raise SystemExit(0)
    days = load()
    if mode == "--sql":
        print(bigquery_sql(days))
    elif mode == "--compare":
        cloud = read_bq(sys.argv[2])
        local = run_local(days)
        try:
            errors = compare(cloud, local)
        except Exception as e:  # report what BigQuery returned (data rows only: no project or account details)
            print(f"::error title=ML.CORRELATION output not understood::{type(e).__name__}: {e}; "
                  f"{len(cloud)} row(s), first: {json.dumps(cloud[:2])[:700]}")
            raise SystemExit(1)
        if errors:
            print(f"::error title=ML.CORRELATION differs from local::{len(errors)} difference(s): "
                  f"{json.dumps(errors)[:800]}")
            raise SystemExit(1)
        s = summarise(local)
        print(f"::notice title=Google Cloud day 14::ML.CORRELATION (PEARSON and SPEARMAN) matched the local "
              f"correlations to 1e-6 for all {len(local)} (method, store, column) rows (SPEARMAN rollup = within-store ranks, pooled): {s['headline']}; "
              f"Spearman {json.dumps(s['correlation']['SPEARMAN'])}")
    else:
        summary = summarise(run_local(days))
        (HERE / "result.json").write_text(json.dumps({"engine": "pure Python (local) / BigQuery ML.CORRELATION (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
