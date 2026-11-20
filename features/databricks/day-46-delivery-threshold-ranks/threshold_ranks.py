"""Day 46 (Databricks): "what share of baskets would qualify for free delivery at $35, $50 or $75?", answered per
store and chain-wide from stored daily **KLL sketches** with the rank function `kll_sketch_get_rank_bigint`.
Databricks Runtime 19 (Beta, 2026-06-15, "KLL quantile functions in SQL"). Day 45 used the quantile direction
(value at a percentile); this day uses the rank direction (percentile of a value), with an array of thresholds in one
call.

  python threshold_ranks.py                          # local: pure-Python reference (exact shares), result.json
  python threshold_ranks.py WORKDIR CATALOG SCHEMA   # serverless job: runs the steps in Unity Catalog, result to WORKDIR

Steps (serverless job, Unity Catalog, Delta):
  1. `baskets` table: 3 stores x 7 days of basket totals in cents (deterministic, generated here)
  2. `basket_sketches`: one kll_sketch_agg_bigint(basket_cents) per store and day (21 sketches)
  3. weekly per store and chain-wide: kll_merge_agg_bigint(sketch), then
     kll_sketch_get_rank_bigint(merged, array(3500L, 5000L, 7500L)) = share of baskets at or under each threshold;
     the share that qualifies for free delivery is 1 - rank.
Must hold: every merged sketch counts exactly the baskets it covers, and every estimated rank sits within EPS of
the exact share at or under the threshold. KLL is randomized, so the third decimal may move from run to run.
"""
from __future__ import annotations

import datetime
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORES = (3, 7, 9)
DAYS = [datetime.date(2026, 11, 9) + datetime.timedelta(days=i) for i in range(7)]
THRESHOLDS = (3500, 5000, 7500)   # cents: candidate free-delivery minimums
EPS = 0.03                        # allowed rank error (KLL k=200: about 1.65% at 99% confidence)
SEED = 46


# ---------- data ----------
def baskets() -> list[tuple[int, datetime.date, int]]:
    """(store_id, sale_date, basket_cents). Weekend baskets are bigger; store 9 is a small-format store; a few
    stock-up trips make a long right tail."""
    rng = random.Random(SEED)
    rows = []
    for si, store in enumerate(STORES):
        for d in DAYS:
            weekend = d.weekday() >= 5
            n = 380 + 60 * si + (140 if weekend else 0) + rng.randint(0, 40)
            mu = {3: 7.9, 7: 8.2, 9: 7.2}[store] + (0.15 if weekend else 0.0)
            for _ in range(n):
                cents = int(rng.lognormvariate(mu, 0.6))
                if rng.random() < 0.02:
                    cents *= 4
                rows.append((store, d, max(cents, 99)))
    return rows


def groups(rows) -> dict[str, list[int]]:
    g: dict[str, list[int]] = {"chain": []}
    for s, _, c in rows:
        g.setdefault(str(s), []).append(c)
        g["chain"].append(c)
    return dict(sorted(g.items()))


def exact_rank(values: list[int], x: int) -> float:
    """Share of values at or under x (the inclusive rank KLL estimates)."""
    return sum(1 for v in values if v <= x) / len(values)


def reference() -> dict:
    g = groups(baskets())
    return {
        "raw_rows": sum(len(v) for k, v in g.items() if k != "chain"),
        "daily_sketches": len(STORES) * len(DAYS),
        "week": [DAYS[0].isoformat(), DAYS[-1].isoformat()],
        "thresholds_cents": list(THRESHOLDS),
        "eps": EPS,
        "exact": {k: {"n": len(v), "rank": [round(exact_rank(v, t), 4) for t in THRESHOLDS],
                      "qualify": [round(1 - exact_rank(v, t), 4) for t in THRESHOLDS]} for k, v in g.items()},
    }


def check(estimates: dict, ref: dict | None = None) -> list[str]:
    """estimates: {group: {"n": int, "rank": [share at or under each threshold]}} from merged sketches."""
    ref = ref or reference()
    errors = []
    for key, exact in ref["exact"].items():
        e = estimates.get(key)
        if e is None:
            errors.append(f"{key}: no merged sketch")
            continue
        if int(e["n"]) != exact["n"]:
            errors.append(f"{key}: sketch n {e['n']} != {exact['n']} baskets")
        ranks = list(e.get("rank") or [])
        if len(ranks) != len(ref["thresholds_cents"]):
            errors.append(f"{key}: {len(ranks)} ranks for {len(ref['thresholds_cents'])} thresholds")
            continue
        if ranks != sorted(ranks):
            errors.append(f"{key}: ranks not increasing with the threshold {ranks}")
        for t, est, x in zip(ref["thresholds_cents"], ranks, exact["rank"]):
            if abs(float(est) - x) > ref["eps"]:
                errors.append(f"{key}: rank at ${t / 100:.0f} estimated {float(est):.3f}, exact {x:.3f}")
    return errors


# ---------- Spark ----------
SKETCH_SQL = """INSERT INTO {sk}
SELECT store_id, sale_date, count(*) AS baskets, kll_sketch_agg_bigint(basket_cents) AS sketch
FROM {raw}
GROUP BY store_id, sale_date"""

RANK_SQL = """WITH merged AS (
  SELECT CAST(store_id AS STRING) AS grp, kll_merge_agg_bigint(sketch) AS m, sum(baskets) AS counted
  FROM {sk} WHERE sale_date BETWEEN DATE'{d0}' AND DATE'{d1}' GROUP BY store_id
  UNION ALL
  SELECT 'chain', kll_merge_agg_bigint(sketch), sum(baskets)
  FROM {sk} WHERE sale_date BETWEEN DATE'{d0}' AND DATE'{d1}'
)
SELECT grp, kll_sketch_get_n_bigint(m) AS n, counted,
       kll_sketch_get_rank_bigint(m, array({ts})) AS rank
FROM merged ORDER BY grp"""


def rank_sql(sk: str) -> str:
    return RANK_SQL.format(sk=sk, d0=DAYS[0], d1=DAYS[-1], ts=", ".join(f"{t}L" for t in THRESHOLDS))


def run_steps(spark, raw: str, sk: str) -> dict:
    from pyspark.sql import types as T
    for t in (raw, sk):
        spark.sql(f"DROP TABLE IF EXISTS {t}")
    schema = T.StructType([T.StructField("store_id", T.IntegerType()), T.StructField("sale_date", T.DateType()),
                           T.StructField("basket_cents", T.LongType())])
    rows = baskets()
    spark.createDataFrame(rows, schema).write.saveAsTable(raw)
    spark.sql(f"CREATE TABLE {sk} (store_id INT, sale_date DATE, baskets BIGINT, sketch BINARY)")
    spark.sql(SKETCH_SQL.format(sk=sk, raw=raw))
    stored = spark.sql(f"SELECT count(*) AS c, sum(length(sketch)) AS b FROM {sk}").collect()[0]
    est = {r.grp: {"n": int(r.n), "counted": int(r.counted), "rank": [round(float(x), 4) for x in r["rank"]]}
           for r in spark.sql(rank_sql(sk)).collect()}
    return {"daily_sketches": int(stored.c), "sketch_bytes": int(stored.b), "raw_rows": len(rows),
            "estimates": est, "errors": check(est)}


def run_uc(spark, catalog: str, schema: str) -> dict:
    return run_steps(spark, f"{catalog}.{schema}.baskets", f"{catalog}.{schema}.basket_sketches")


def main(argv: list[str]) -> int:
    if argv:  # serverless job: WORKDIR CATALOG SCHEMA
        import pyspark
        from pyspark.sql import SparkSession
        out = run_uc(SparkSession.builder.getOrCreate(), argv[1], argv[2])
        out["engine"] = f"KLL sketch ranks in Unity Catalog Delta on a Databricks serverless job (pyspark {pyspark.__version__})"
        text = json.dumps(out, sort_keys=True)
        print("DAY46_RESULT " + text)
        Path(argv[0].rstrip("/")).mkdir(parents=True, exist_ok=True)
        Path(argv[0].rstrip("/"), "result.json").write_text(text)
        return 0
    out = {"engine": "pure-Python exact shares (local reference) / KLL sketch ranks on a Databricks serverless job "
                     "(cloud)", **reference()}
    (HERE / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out["exact"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
