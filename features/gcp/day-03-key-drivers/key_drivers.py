"""Day 3 (Google Cloud): what drove this week's change in grocery units sold? BigQuery's new AI.KEY_DRIVERS.

  python key_drivers.py --local          # every segment (CUBE over store x category x channel) in DuckDB; writes result.json
  python key_drivers.py --sql            # prints the BigQuery query (data inlined, nothing to load or clean up)
  python key_drivers.py --compare FILE   # compares BigQuery's JSON output with the local result

Columns checked exactly: segment, metric_interest, metric_reference, difference, apriori_support, contribution.
unexpected_difference is checked against the local formula below and reported as a warning if it differs.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIMS = ["store", "category", "channel"]
EXACT = ["metric_interest", "metric_reference", "difference", "apriori_support", "contribution"]
DIGITS = 6


def load_rows() -> list[dict]:
    with open(HERE / "data" / "weekly_units.csv", newline="") as f:
        return list(csv.DictReader(f))


def bigquery_sql(rows: list[dict]) -> str:
    def lit(s: str) -> str:
        return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
    structs = [f"    STRUCT({lit(r['store'])} AS store, {lit(r['category'])} AS category, {lit(r['channel'])} AS channel, "
               f"{lit(r['week'])} AS week, {int(r['units'])} AS units)" if i == 0 else
               f"    ({lit(r['store'])}, {lit(r['category'])}, {lit(r['channel'])}, {lit(r['week'])}, {int(r['units'])})"
               for i, r in enumerate(rows)]
    return (HERE / "key_drivers.sql").read_text().replace("{values}", ",\n".join(structs))


def segment_key(drivers) -> str:
    """'all' or sorted 'col=value' parts joined by ' & ', from a list or a '[a=b,c=d]' string."""
    if isinstance(drivers, str):
        drivers = [p for p in drivers.strip("[]").split(",") if p]
    parts = sorted(p.strip() for p in drivers if p.strip() and p.strip() != "all")
    return " & ".join(parts) or "all"


def run_local(rows: list[dict]) -> dict[str, dict]:
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE t(store VARCHAR, category VARCHAR, channel VARCHAR, week VARCHAR, units BIGINT)")
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?)",
                    [(r["store"], r["category"], r["channel"], r["week"], int(r["units"])) for r in rows])
    cube = con.sql("""
        SELECT store, category, channel,
               SUM(units) FILTER (WHERE week = 'this_week') AS i,
               SUM(units) FILTER (WHERE week = 'last_week') AS r
        FROM t GROUP BY CUBE (store, category, channel)""").fetchall()
    total_i = next(c[3] for c in cube if c[:3] == (None, None, None))
    total_r = next(c[4] for c in cube if c[:3] == (None, None, None))
    out = {}
    for store, category, channel, i, r in cube:
        key = segment_key([f"{d}={v}" for d, v in zip(DIMS, (store, category, channel)) if v is not None])
        # Expected interest value if the segment had changed like the rest of the data (its complement).
        rest_i, rest_r = total_i - i, total_r - r
        unexpected = None if rest_r == 0 else i - r * rest_i / rest_r
        out[key] = {"metric_interest": float(i), "metric_reference": float(r), "difference": float(i - r),
                    "apriori_support": max(i / total_i, r / total_r), "contribution": float(abs(i - r)),
                    "unexpected_difference": unexpected}
    return {k: {f: (None if v is None else round(v, DIGITS)) for f, v in d.items()} for k, d in out.items()}


def read_bq(path: str) -> dict[str, dict]:
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a warning line before the JSON
    try:
        rows = json.loads(text[start:])
        return {segment_key(r["drivers"]): {f: (None if r.get(f) is None else round(float(r[f]), DIGITS))
                                           for f in EXACT + ["unexpected_difference"]} for r in rows}
    except (ValueError, KeyError, TypeError) as e:
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {text[:300]!r}")
        raise SystemExit(1)


def close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-4)


def summarise(seg: dict[str, dict]) -> dict:
    """Headline: overall change and the two-dimension segments that explain the most of it (by contribution)."""
    pairs = sorted((k for k in seg if k.count("&") == 1), key=lambda k: (-seg[k]["contribution"], k))[:2]
    a = seg["all"]
    return {
        "segments": len(seg),
        "units_last_week": int(a["metric_reference"]),
        "units_this_week": int(a["metric_interest"]),
        "change_pct": round(100 * a["difference"] / a["metric_reference"], 1),
        "top_drivers": [{"segment": k, "difference": int(seg[k]["difference"])} for k in pairs],
    }


if __name__ == "__main__":
    rows = load_rows()
    local = run_local(rows)
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(bigquery_sql(rows))
    elif mode == "--compare":
        cloud = read_bq(sys.argv[2])
        missing, extra = sorted(set(local) - set(cloud)), sorted(set(cloud) - set(local))
        bad = [(k, f, cloud[k][f], local[k][f]) for k in set(local) & set(cloud) for f in EXACT
               if not close(cloud[k][f], local[k][f])]
        if missing or extra or bad:
            print(f"::error title=BigQuery AI.KEY_DRIVERS differs from local::"
                  f"{json.dumps({'missing': missing[:5], 'extra': extra[:5], 'diff': bad[:5]})[:900]}")
            raise SystemExit(1)
        ud = [(k, cloud[k]["unexpected_difference"], local[k]["unexpected_difference"]) for k in local
              if not close(cloud[k]["unexpected_difference"], local[k]["unexpected_difference"])]
        if ud:
            print(f"::warning title=AI.KEY_DRIVERS unexpected_difference differs from the local formula::"
                  f"{len(ud)} segment(s), e.g. {json.dumps(sorted(ud)[:3])[:600]}")
        print(f"::notice title=Google Cloud day 3::AI.KEY_DRIVERS ran on BigQuery and matched local on "
              f"{len(cloud)} segments: {json.dumps(summarise(cloud))}")
    else:
        summary = summarise(local)
        top = sorted(local.items(), key=lambda kv: (-kv[1]["contribution"], kv[0]))[:8]
        (HERE / "result.json").write_text(json.dumps({"engine": "duckdb CUBE (local) / BigQuery AI.KEY_DRIVERS (cloud)",
                                                      **summary,
                                                      "top_by_contribution": [{"segment": k, **v} for k, v in top]},
                                                     indent=2) + "\n")
        print(json.dumps(summary, indent=2))
