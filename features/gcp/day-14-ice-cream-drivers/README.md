# Day 14 · Google Cloud · What drives ice cream sales, with ML.CORRELATION

**New platform feature:** the **`ML.CORRELATION`** function in BigQuery ML, **Preview**, announced
**2026-09-10** in the [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)
([reference](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-correlation)):
it calculates correlations between a target column and one or more metric columns, sliced by up to 12 dimension
columns, with `PEARSON` (default), `SPEARMAN` or `KENDALL`. No model is trained.

## What it does
28 days of September 2026 in 3 stores (84 rows): daily ice cream revenue, the day's high temperature, the promotion
depth and foot traffic. Each store has a different driver planted in the data. One query, data inlined, calls
`ML.CORRELATION` twice:

```sql
SELECT 'PEARSON' AS method, IF(ARRAY_LENGTH(segment) = 0, 'ALL', store) AS store, corr_col, correlation, segment_size
FROM ML.CORRELATION(
  (SELECT store, ice_cream_revenue, max_temp_c, promo_pct, foot_traffic FROM days),
  target_col => 'ice_cream_revenue',
  target_correlation_cols => ['max_temp_c', 'promo_pct', 'foot_traffic'],
  dimension_cols => ['store'],
  method => 'PEARSON')
UNION ALL
SELECT 'SPEARMAN', ... method => 'SPEARMAN')
```
With `dimension_cols => ['store']` the function returns every store plus the overall rollup (its `segment` array is
empty), so 2 methods x 4 segments x 3 columns = 24 rows. A third call, `SPEARMAN` without `dimension_cols`, adds the
all-rows Spearman (3 more rows; see the finding below). Locally, pure Python computes the same Pearson and Spearman
values (the test cross-checks them with numpy). No value repeats in any column, so Spearman needs no tie rule and
the local ranks must match BigQuery's exactly.

## Why it matters
"What moves this number, and is it the same everywhere?" is the first question about any sales series. Before, it
meant one `CORR()` per column per `GROUP BY`, and rank correlation meant computing ranks by hand. Here, one call
returns the whole grid, rollup included.

## How to run
```bash
python features/gcp/day-14-ice-cream-drivers/drivers.py --local     # pure Python; writes result.json
python features/gcp/day-14-ice-cream-drivers/drivers.py --sql       # print the BigQuery SQL
pytest -q tests/test_day14_gcp_ml_correlation.py
```
`cloud.sh` runs the query with `bq` in GitHub Actions (keyless) and fails on a missing or extra row, a different
segment size, or any correlation more than 1e-6 from local.

## Cost
One query over a few KB of inlined data, capped at 20 MiB billed: free tier. Nothing is created.

## Result
| Segment | temperature | promotion | foot traffic |
|---|---|---|---|
| Lakeside (S1) | **0.964** | 0.040 | 0.354 |
| Downtown (S2) | 0.146 | **0.960** | 0.093 |
| Campus (S3) | 0.100 | 0.118 | **0.905** |
| All stores | 0.479 | 0.344 | 0.362 |

Pearson correlation with daily ice cream revenue. Each store's driver comes out clearly, but pooled, the weather
shows only r = 0.48: a chain-level correlation would hide all three stories. Spearman agrees on the drivers;
Downtown's promotion drops to 0.80 under Spearman because promotions come in a few fixed levels, so the rank
relationship is looser than the linear one.

**Finding from the first cloud run (2026-10-18):** all Pearson rows and the per-store Spearman rows matched, but
the three Spearman **rollup** rows did not. BigQuery's rollup Spearman is the Pearson correlation of ranks taken
**within each store**, pooled (it matched that to 1e-15), not the Spearman over all rows:

| Overall Spearman | temperature | promotion | foot traffic |
|---|---|---|---|
| rollup row, `dimension_cols => ['store']` | 0.462 | 0.319 | 0.476 |
| all rows, no `dimension_cols` | 0.505 | 0.334 | 0.384 |

So the rollup row answers "within a store, do higher-ranked days go together?", which is a reasonable question but
not what the empty `segment` suggests; for the chain-wide rank correlation, call without `dimension_cols`. The local
model and the check now cover both (fixed forward the same day).

**Cloud run (2026-10-18, after the fix):** all 27 rows (Pearson, per-store Spearman, the within-store Spearman
rollup and the all-rows Spearman without `dimension_cols`) matched local to 1e-6.

Stores and sales are made up. Full numbers in [`result.json`](result.json).
