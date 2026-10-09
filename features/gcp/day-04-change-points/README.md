# Day 4 · Google Cloud · When did oat-milk sales shift? ML.DETECT_CHANGE_POINTS

**New platform feature:** [`ML.DETECT_CHANGE_POINTS`](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-detect-change-points)
(Preview), released **2026-08-20** together with `ML.TREND` and `ML.SEASONALITY`
([BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)). A table-valued function
that finds the time windows where a series shifts, with no model to create or delete.

## What it does
91 days (2026-06-01 to 2026-08-30) of daily oat-milk units for two stores. East moved oat milk to an end-cap on
2026-07-10; west changed nothing. One query runs the function over both series at once:

```sql
SELECT * FROM ML.DETECT_CHANGE_POINTS(
  TABLE daily, data_col => 'units', timestamp_col => 'sale_date', id_cols => ['store'])
```

Each output row is a change window (`begin_timestamp`, `end_timestamp`) with `metrics` (avg, min, max, stddev,
count) over that window. The data is inlined in the query, so there is no dataset to create or clean up.

## Why it matters
Category managers want to know *when* a product's demand moved, not just that it did: was it the shelf move, the
price change or the competitor opening? Doing that per product and store used to mean exporting series to a notebook
or training a model per series. Here it's one SQL call over every series, tuned for sustained shifts rather than
one-day spikes (a promo day isn't a change point).

## How to run
```bash
python change_points.py --local          # pure-Python mean-shift detector; writes result.json
python change_points.py --sql            # the BigQuery query
python change_points.py --compare out.json
```
In CI, `cloud.sh` runs the query with `bq` (stdin, `--maximum_bytes_billed=20971520`) and checks it against local.
BigQuery doesn't publish the algorithm, so "matches local" means: no error `status`; an east window contains the
local change date (2026-07-10); each window's `metrics` are recomputed locally over its own dates (a mismatch is a
warning that records BigQuery's definition); windows on west, a flat series, are reported as a warning.
The test (`tests/test_day04_gcp_change_points.py`) covers the detector and the checker with synthetic BigQuery output.

## Cost
One query over a few KB, capped at 20 MiB billed: inside the BigQuery free tier. Nothing is created.

## Result (synthetic, seeded series)
| Store | Local change | Avg before | Avg after | Shift | BigQuery change window |
|---|---|---|---|---|---|
| east | 2026-07-10 | 40.4 | 62.8 | +55.3% | 2026-06-27 to 2026-07-11 (metrics match local) |
| west | none | | | | none (one row with NULL timestamps) |

The local detector (best single least-squares split, kept only if the shift is at least 3 within-segment standard
deviations and each side at least a week) finds the shelf move on the exact day. BigQuery (cloud run 2026-10-08) put east's change in a 15-day window
ending the day after the move, and reported no change for west. A series without a change point comes back as a
single row with NULL `begin_timestamp`, `end_timestamp` and `metrics`.
