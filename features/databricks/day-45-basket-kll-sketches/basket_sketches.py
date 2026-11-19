"""Day 45 (Databricks): weekly basket-size percentiles per store from mergeable daily **KLL quantile sketches**.
Databricks Runtime 19 (Beta, 2026-06-15, "KLL quantile functions in SQL"): SQL natively supports KLL quantile
sketch functions powered by Apache DataSketches: `kll_sketch_agg_bigint`, `kll_merge_agg_bigint`,
`kll_sketch_get_quantile_bigint`, `kll_sketch_get_rank_bigint`, `kll_sketch_get_n_bigint`.

  python basket_sketches.py                          # local: pure-Python reference (exact percentiles), result.json
  python basket_sketches.py WORKDIR CATALOG SCHEMA   # serverless job: runs the steps in Unity Catalog, result to WORKDIR

The story: each night the chain stores one small binary sketch per store and day instead of keeping every basket
total around for percentile reports. On Monday the weekly basket-size percentiles (median, p90, p99) per store and
for the whole chain come from merging the stored daily sketches only; no basket rows are re-scanned.

Steps (serverless job, Unity Catalog, Delta):
  1. `baskets` table: 3 stores x 7 days of basket totals in cents (deterministic, generated here)
  2. `basket_sketches`: INSERT ... SELECT store_id, sale_date, count(*), kll_sketch_agg_bigint(basket_cents)
     GROUP BY store_id, sale_date (21 sketches)
  3. weekly per store and chain-wide: kll_merge_agg_bigint(sketch) over the sketch table, then
     kll_sketch_get_n_bigint and kll_sketch_get_quantile_bigint(merged, array(0.5, 0.9, 0.99))
Must hold: every merged sketch counts exactly the baskets it covers, and every estimated percentile sits within
EPS (normalized rank) of the requested one in the exact data. KLL is randomized, so values may differ from run to
run by a few cents; the rank check, not equality, is the contract.
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
QUANTILES = (0.5, 0.9, 0.99)
EPS = 0.03            # allowed normalized-rank error (KLL k=200: about 1.65% at 99% confidence)
SEED = 45


# ---------- data ----------
def baskets() -> list[tuple[int, datetime.date, int]]:
    """(store_id, sale_date, basket_cents). Weekend days are busier and baskets bigger; store 9 is a small-format
    store with small baskets; a few stock-up trips make a long right tail."""
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
                    cents *= 4  # stock-up trip
                rows.append((store, d, max(cents, 99)))
    return rows


# ---------- exact reference ----------
def exact_quantile(values: list[int], q: float) -> int:
    """Smallest value whose cumulative fraction is >= q (inverse CDF)."""
    v = sorted(values)
    k = max(0, min(len(v) - 1, int(-(-q * len(v) // 1)) - 1))
    return v[k]


def rank_window(values: list[int], x: int) -> tuple[float, float]:
    """(fraction strictly below x, fraction at or below x)."""
    n = len(values)
    return sum(1 for v in values if v < x) / n, sum(1 for v in values if v <= x) / n


def groups(rows) -> dict[str, list[int]]:
    g: dict[str, list[int]] = {"chain": []}
    for s, _, c in rows:
        g.setdefault(str(s), []).append(c)
        g["chain"].append(c)
    return dict(sorted(g.items()))


def reference() -> dict:
    rows = baskets()
    g = groups(rows)
    return {
        "raw_rows": len(rows),
        "daily_sketches": len(STORES) * len(DAYS),
        "week": [DAYS[0].isoformat(), DAYS[-1].isoformat()],
        "quantiles": list(QUANTILES),
        "eps": EPS,
        "exact": {k: {"n": len(v), "p": [exact_quantile(v, q) for q in QUANTILES],
                      "mean_cents": round(sum(v) / len(v))} for k, v in g.items()},
    }


def check(estimates: dict, ref: dict | None = None) -> list[str]:
    """estimates: {group: {"n": int, "p": [cents per quantile]}} from merged sketches."""
    ref = ref or reference()
    g = groups(baskets())
    errors = []
    for key, exact in ref["exact"].items():
        e = estimates.get(key)
        if e is None:
            errors.append(f"{key}: no merged sketch")
            continue
        if int(e["n"]) != exact["n"]:
            errors.append(f"{key}: sketch n {e['n']} != {exact['n']} baskets")
        for q, est in zip(ref["quantiles"], e["p"]):
            lo, hi = rank_window(g[key], int(est))
            if not (lo - ref["eps"] <= q <= hi + ref["eps"]):
                errors.append(f"{key}: p{round(q * 100)} estimate {est} has rank {lo:.3f}..{hi:.3f}, "
                              f"outside {q} +/- {ref['eps']}")
    return errors


# ---------- Spark ----------
SKETCH_SQL = """INSERT INTO {sk}
SELECT store_id, sale_date, count(*) AS baskets, kll_sketch_agg_bigint(basket_cents) AS sketch
FROM {raw}
GROUP BY store_id, sale_date"""

WEEKLY_SQL = """WITH merged AS (
  SELECT CAST(store_id AS STRING) AS grp, kll_merge_agg_bigint(sketch) AS m, sum(baskets) AS counted
  FROM {sk} WHERE sale_date BETWEEN DATE'{d0}' AND DATE'{d1}' GROUP BY store_id
  UNION ALL
  SELECT 'chain', kll_merge_agg_bigint(sketch), sum(baskets)
  FROM {sk} WHERE sale_date BETWEEN DATE'{d0}' AND DATE'{d1}'
)
SELECT grp, kll_sketch_get_n_bigint(m) AS n, counted,
       kll_sketch_get_quantile_bigint(m, array({qs})) AS p,
       length(m) AS merged_bytes
FROM merged ORDER BY grp"""


def run_steps(spark, raw: str, sk: str) -> dict:
    from pyspark.sql import types as T
    for t in (raw, sk):
        spark.sql(f"DROP TABLE IF EXISTS {t}")  # leftovers from an interrupted run
    schema = T.StructType([T.StructField("store_id", T.IntegerType()), T.StructField("sale_date", T.DateType()),
                           T.StructField("basket_cents", T.LongType())])
    spark.createDataFrame(baskets(), schema).write.saveAsTable(raw)
    spark.sql(f"CREATE TABLE {sk} (store_id INT, sale_date DATE, baskets BIGINT, sketch BINARY)")
    spark.sql(SKETCH_SQL.format(sk=sk, raw=raw))
    stored = spark.sql(f"SELECT count(*) AS c, sum(length(sketch)) AS b FROM {sk}").collect()[0]
    rows = spark.sql(WEEKLY_SQL.format(sk=sk, d0=DAYS[0], d1=DAYS[-1],
                                       qs=", ".join(str(q) for q in QUANTILES))).collect()
    est = {r.grp: {"n": int(r.n), "counted": int(r.counted), "p": [int(x) for x in r.p],
                   "merged_bytes": int(r.merged_bytes)} for r in rows}
    return {"daily_sketches": int(stored.c), "sketch_bytes": int(stored.b), "raw_rows": len(baskets()),
            "raw_bytes": 8 * len(baskets()), "estimates": est, "errors": check(est)}


def run_uc(spark, catalog: str, schema: str) -> dict:
    return run_steps(spark, f"{catalog}.{schema}.baskets", f"{catalog}.{schema}.basket_sketches")


def main(argv: list[str]) -> int:
    if argv:  # serverless job: WORKDIR CATALOG SCHEMA
        import pyspark
        from pyspark.sql import SparkSession
        out = run_uc(SparkSession.builder.getOrCreate(), argv[1], argv[2])
        out["engine"] = f"KLL sketches in Unity Catalog Delta on a Databricks serverless job (pyspark {pyspark.__version__})"
        text = json.dumps(out, sort_keys=True)
        print("DAY45_RESULT " + text)  # read back from the task's logs if the output file can't be read
        Path(argv[0].rstrip("/")).mkdir(parents=True, exist_ok=True)
        Path(argv[0].rstrip("/"), "result.json").write_text(text)
        return 0
    out = {"engine": "pure-Python exact percentiles (local reference) / KLL sketches merged on a Databricks "
                     "serverless job (cloud)", **reference()}
    (HERE / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out["exact"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
