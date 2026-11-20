# Day 46 · Databricks · Free-delivery threshold shares from KLL sketch ranks

**New platform feature:** **KLL quantile functions in SQL** (Databricks Runtime 19, Beta, **2026-06-15**), rank side:
`kll_sketch_get_rank_bigint` ("aggregation and CDF computation functions for approximate quantile queries"). From the
[Databricks Runtime 19 release notes](https://docs.databricks.com/gcp/en/release-notes/runtime/19). Day 45 used the
quantile direction (value at a percentile); this day uses the CDF direction (share of baskets at or under a dollar
amount), with an array of thresholds in one call. Functions used: `kll_sketch_agg_bigint`, `kll_merge_agg_bigint`,
`kll_sketch_get_n_bigint`, `kll_sketch_get_rank_bigint`.

## What it does
The chain is choosing a free-delivery minimum: **what share of baskets would qualify at $35, $50 or $75?** The answer
per store and chain-wide comes from merging stored daily sketches, never from the basket rows.

| Step | SQL |
|---|---|
| 1. `baskets`: 3 stores x 7 days (2026-11-09..15), 10,506 basket totals in cents | generated deterministically in [`threshold_ranks.py`](threshold_ranks.py) |
| 2. `basket_sketches`: 21 rows `(store_id, sale_date, baskets, sketch BINARY)` | `kll_sketch_agg_bigint(basket_cents) ... GROUP BY store_id, sale_date` |
| 3. Weekly per store and chain-wide | `kll_merge_agg_bigint(sketch)` → `kll_sketch_get_rank_bigint(m, array(3500L, 5000L, 7500L))` |

Share that qualifies = 1 − rank. Runs as a one-off serverless job (environment version 6) in a scratch schema that is
dropped on exit.

**Must hold** (else the cloud run fails): every merged sketch counts exactly its baskets, ranks increase with the
threshold, and every estimated rank is within **0.03** of the exact share in the local data.

## Why it matters
"How many orders clear $X?" is a CDF question; with sketches it's a merge plus one function call for any week, store
group or threshold list, without rescanning baskets. It reuses the same stored sketches as percentile reports.

## How to run
```bash
python features/databricks/day-46-delivery-threshold-ranks/threshold_ranks.py   # exact reference, writes result.json
pytest -q tests/test_day46_databricks_kll_ranks.py                              # no credentials (local Spark 4.3 + fakes)
bash features/databricks/day-46-delivery-threshold-ranks/cloud.sh               # GitHub Actions: DATABRICKS_HOST / TOKEN
```

## Cost
One short serverless job (~10.5k rows, 21 sketches, one rank query): within Free Edition's serverless quota. Scratch
schema, volume and workspace folder are deleted on exit.

## Result
Share of baskets **over** each threshold, exact vs. merged sketches from one local open-source Spark 4.3 run (same code
path):

| Group | Baskets | over $35 exact / sketch | over $50 exact / sketch | over $75 exact / sketch |
|---|---:|---:|---:|---:|
| Store 3 | 3,092 | 37.3% / 37.3% | 18.4% / 18.4% | 6.2% / 6.0% |
| Store 7 | 3,511 | 56.7% / 56.2% | 33.7% / 33.4% | 14.5% / 14.1% |
| Store 9 (small format) | 3,903 | 8.3% / 7.9% | 2.6% / 2.5% | 0.7% / 0.9% |
| Chain | 10,506 | 33.0% / 32.9% | 17.7% / 17.6% | 7.0% / 6.7% |

All 12 ranks are within 0.005 of exact (bound 0.03). A $50 minimum would cover about 1 in 6 baskets chain-wide but only
1 in 40 at the small-format store. The cloud run is refused while the Free Edition account is inactive (see the
report), so this is verified locally only for now. Numbers in [`result.json`](result.json).
