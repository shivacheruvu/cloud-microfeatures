# Day 5 · Google Cloud · Which weekday sells the most bread? ML.SEASONALITY

**New platform feature:** [`ML.SEASONALITY`](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-seasonality),
**Preview**, released **2026-08-20** ([BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)).
A table-valued function that returns the seasonal components (yearly, quarterly, monthly, weekly, daily) of a time
series row by row, with no model to create; `id_cols` runs one decomposition per series.

## What it does
Twelve weeks of daily bread sales (loaves) for two stores go into one query, inlined, and one call:

```sql
SELECT * FROM ML.SEASONALITY(
  TABLE daily, data_col => 'units', timestamp_col => 'sale_date',
  id_cols => ['store'], seasonalities => ['WEEKLY'])
```

Each row comes back with its `weekly` component. Averaging that per weekday gives each store's weekly profile:
how many loaves above or below its trend a Monday, a Saturday, etc. sells.

Locally, a classical decomposition (centred 7-day moving average as the trend, average distance from it per
weekday, centred to zero) computes the same profile in pure Python.

## Why it matters
Bake plans and shelf fills are set per weekday. Two stores that sell similar weekly totals can need very different
days: north is a Saturday big-shop store, south an after-work Friday store. A weekday profile separated from the
trend (both stores grow through the summer) tells the bakery how much extra to bake on which day, and one call
covers every store.

## How to run
```bash
python seasonality.py --local          # pure Python; writes result.json
python seasonality.py --sql            # prints the BigQuery query
python seasonality.py --compare FILE   # checks bq JSON output against the local profile
```
In CI, `cloud.sh` signs in keylessly, runs the query with `bq` (capped at 20 MiB billed, `--max_rows=1000` because
the output has 168 rows), and checks it. BigQuery's decomposition is its own, so it fails on a non-empty `status`,
a store with no weekly component, or a **peak weekday** different from local; trough day, profile correlation
(< 0.95) and amplitude (off by > 30%) are warnings. The test (`tests/test_day05_gcp_seasonality.py`) checks the
local profile against the seeded pattern and the comparison on agreeing and wrong BigQuery-shaped outputs.

## Cost
One query over a few KB of inlined data (BigQuery on-demand free tier: 1 TiB a month). Nothing is created, so
nothing to clean up.

## Result (synthetic, seeded 2026-06-29 to 2026-09-20)
| Store | Peak day | Loaves above trend | Trough day | Peak-to-trough |
|---|---|---|---|---|
| north | Sat | +57 | Tue (-33) | 90 |
| south | Fri | +37 | Mon (-11; Tue -11 too) | 48 |

North's full profile: Mon -28, Tue -33, Wed -22, Thu -4, Fri +22, Sat +57, Sun +9. South's Monday and Tuesday are
within a loaf of each other, which is why the trough is only a warning in the cloud check.
