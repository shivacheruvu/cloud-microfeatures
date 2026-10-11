# Day 6 · Google Cloud · Is oat milk growing, or just noisy? ML.TREND

**New platform feature:** [`ML.TREND`](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-trend)
(Preview), released **2026-08-20** ([BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)).
A table-valued function that returns the trend component of one or many time series (`id_cols`), with the weekly
pattern taken out, and can extend it `horizon` points ahead. No model to create, nothing to clean up. It's the
sibling of day 5's `ML.SEASONALITY`.

## What it does
112 days (2026-06-01 to 2026-09-20) of daily oat-milk units for two stores, both with a strong weekend pattern and
day-to-day noise, go into one query (inlined, no dataset):

```sql
SELECT * FROM ML.TREND(TABLE daily, data_col => 'units', timestamp_col => 'sale_date',
                       id_cols => ['store'], horizon => 14)
```

A least-squares line through each store's trend gives units per week. A store is **growing** at +1.5 units/week or
more, **declining** at -1.5 or less, otherwise **flat**. Locally, a centred 7-day moving average (a full week in every
window, so the weekday pattern cancels) gives the same classification without the cloud.

## Why it matters
Raw daily sales swing ±25% between a Saturday and a Tuesday, so "is this product taking off?" is hard to see by eye
and easy to over-read from one good weekend. One SQL call gives the shelf-space or reorder decision a trend line
per store, and the 14-day horizon gives a first number to plan against.

## How to run
```bash
python trend.py --local            # pure Python; writes result.json
python trend.py --sql              # the BigQuery query
python trend.py --compare out.json # check bq --format=json output against local
```
In CI, `cloud.sh` runs the query on BigQuery (keyless, `--maximum_bytes_billed` 20 MiB) and fails if `status` is
set, a store has no trend, or BigQuery's class differs from local; a slope more than max(1 unit/week, 35%) away,
a forecast that isn't 14 rows or runs the other way are warnings. The test
(`tests/test_day06_gcp_trend.py`) checks the local trend and the comparison logic on made-up BigQuery output.

## Cost
One query over a few KB of inlined data, capped at 20 MiB billed: inside the BigQuery free tier. Nothing created.

## Result (synthetic, local)
| Store | Trend | Slope | First-week avg | Last-week avg |
|---|---|---|---|---|
| north | **growing** | +3.4 units/week | 41.2/day | 90.1/day |
| south | flat | +0.3 units/week | 42.9/day | 45.7/day |
