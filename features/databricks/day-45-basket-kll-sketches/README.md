# Day 45 · Databricks · Weekly basket-size percentiles from mergeable daily KLL sketches

**New platform feature:** **KLL quantile functions in SQL** (Databricks Runtime 19, Beta, **2026-06-15**): "SQL now
natively supports KLL quantile sketch functions powered by Apache DataSketches, including aggregation and CDF
computation functions for approximate quantile queries." From the
[Databricks Runtime 19 release notes](https://docs.databricks.com/gcp/en/release-notes/runtime/19) (Apache Spark
fixes and improvements). Functions used: `kll_sketch_agg_bigint`, `kll_merge_agg_bigint`,
`kll_sketch_get_n_bigint`, `kll_sketch_get_quantile_bigint`.

## What it does
Each night the chain keeps **one small binary sketch per store and day** of basket totals instead of the basket rows.
On Monday the weekly basket-size percentiles come from merging those sketches only.

| Step | SQL |
|---|---|
| 1. `baskets`: 3 stores x 7 days (2026-11-09..15), 10,538 basket totals in cents | generated deterministically in [`basket_sketches.py`](basket_sketches.py) |
| 2. `basket_sketches`: 21 rows `(store_id, sale_date, baskets, sketch BINARY)` | `INSERT ... SELECT ..., kll_sketch_agg_bigint(basket_cents) ... GROUP BY store_id, sale_date` |
| 3. Weekly per store and chain-wide | `kll_merge_agg_bigint(sketch)` → `kll_sketch_get_n_bigint`, `kll_sketch_get_quantile_bigint(m, array(0.5, 0.9, 0.99))` |

The weekly query never touches `baskets`. It runs as a one-off serverless job (environment version 6) in a scratch
schema that is dropped on exit.

**Must hold** (else the cloud run fails): every merged sketch counts exactly the baskets it covers (and agrees with
the per-day counts), and every estimated percentile lies within **0.03 normalized rank** of the requested one in the
exact data. KLL sketches are randomized, so the estimate can move by a few cents between runs; the rank bound, not
the value, is the contract.

## Why it matters
Percentiles don't add up: you can't average daily medians into a weekly median, so percentile reports usually rescan
every row for every period and every rollup (store, region, chain). Sketches merge: keep one per store-day and any
week, month or region is a merge away, with a known error bound. Before this release that meant a third-party JAR or a
Python UDF; now it's built-in SQL on serverless and Free Edition.

## How to run
```bash
python features/databricks/day-45-basket-kll-sketches/basket_sketches.py   # exact reference, writes result.json
pytest -q tests/test_day45_databricks_kll_sketches.py                      # no credentials (local Spark 4.3 + fakes)
bash features/databricks/day-45-basket-kll-sketches/cloud.sh               # GitHub Actions: DATABRICKS_HOST / TOKEN
```

## Cost
One short serverless job (writes ~10.5k rows, 21 sketches, one merge query): within Free Edition's serverless quota.
Scratch schema (with both tables), volume and workspace folder are deleted on exit.

## Result
Exact week (reference) vs. merged sketches from one local open-source Spark 4.3 run (same code path; Spark has had these
functions since 4.1):

| Group | Baskets | p50 exact / sketch | p90 exact / sketch | p99 exact / sketch |
|---|---:|---:|---:|---:|
| Store 3 | 3,156 | $29.03 / $28.92 | $62.35 / $62.40 | $134.12 / $131.34 |
| Store 7 | 3,537 | $38.34 / $38.25 | $90.83 / $90.85 | $211.12 / $181.92 |
| Store 9 (small format) | 3,845 | $14.13 / $14.04 | $32.36 / $32.29 | $75.84 / $73.15 |
| Chain | 10,538 | $25.21 / $25.03 | $65.52 / $65.09 | $156.40 / $141.55 |

All 12 estimates are inside the rank bound. The weekly merged sketch is about **3.8 KB** per group vs. ~25-30 KB of raw
8-byte values (21 daily sketches: 44 KB in all). p99 dollar values move more because the tail is sparse (stock-up
trips), which is exactly why the check is on rank. The cloud run is refused while the Free Edition account is
inactive (see the report), so this is verified locally only for now. Numbers in [`result.json`](result.json).
