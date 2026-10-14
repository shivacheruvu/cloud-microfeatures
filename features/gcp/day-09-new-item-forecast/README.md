# Day 9 · Google Cloud · How much will a new item sell? Zero-shot `AI.PREDICT` with TabFM

**New platform feature:** **TabFM** in BigQuery (Preview, **2026-08-31**): zero-shot tabular regression and
classification through `AI.PREDICT` and `AI.EVALUATE`, with no model to train or store.
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes), entry of 2026-08-31;
[`AI.PREDICT` reference](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-predict).

## What it does
48 known grocery items (category, price, shelf level, on promo, organic, weekly units) and 8 new items about to go on
the shelf. One query hands the known items to TabFM as context and predicts weekly units for the new ones:

```sql
SELECT * FROM AI.PREDICT(
  (SELECT * FROM UNNEST([STRUCT('dairy' AS category, 4.97 AS price, 3 AS shelf_level, TRUE AS on_promo,
                                TRUE AS organic, 308.6 AS weekly_units), ...])),   -- known items
  (SELECT * FROM UNNEST([STRUCT('N048' AS sku, 'beverages' AS category, 7.23 AS price, ...), ...])),  -- new items
  label_col => 'weekly_units')
```

The new items' first-week sales are known but never sent (held out), so the forecast can be scored. Locally, a
pure-Python least-squares model on log(units) is the yardstick, and "every new item sells the known average" is
the naive baseline.

## Why it matters
Ordering for a new item is a guess today. A regression model would need `CREATE MODEL`, training time, a stored
model to manage and someone to own it. TabFM answers from the table you already have, in one query, with nothing
left behind.

## How to run
```bash
python features/gcp/day-09-new-item-forecast/forecast.py --local     # least squares + naive; writes result.json
python features/gcp/day-09-new-item-forecast/forecast.py --sql       # print the BigQuery query
pytest -q tests/test_day09_gcp_tabfm_predict.py
```
`cloud.sh` runs the query in GitHub Actions (keyless). TabFM isn't deterministic, so the check is on properties: all
8 new items get a positive, finite forecast, and TabFM's mean absolute error must beat the naive average (an error
otherwise); it warns if TabFM's error is more than 1.5x the local least-squares model's.

## Cost
A few KB of inlined data, capped at 20 MiB billed. During Preview, on-demand TabFM usage is billed by bytes
processed (free tier). TabFM moves to token-based pricing on 2026-10-30; this check will need a cost note then.
Nothing is created.

## Result (local)
| New item | Category | Price | Shelf | Promo | Organic | Actual | Least squares | Naive |
|---|---|---|---|---|---|---|---|---|
| N048 | beverages | 7.23 | 2 | yes | yes | 131.5 | 124.6 | 273.5 |
| N049 | bakery | 8.71 | 4 | yes | yes | 67.6 | 62.0 | 273.5 |
| N050 | dairy | 6.04 | 2 | | | 146.9 | 159.1 | 273.5 |
| N051 | produce | 1.79 | 2 | | | 964.0 | 887.0 | 273.5 |
| N052 | snacks | 8.48 | 3 | | yes | 74.8 | 75.9 | 273.5 |
| N053 | beverages | 3.35 | 3 | | yes | 308.7 | 274.3 | 273.5 |
| N054 | bakery | 4.49 | 2 | yes | yes | 154.3 | 153.8 | 273.5 |
| N055 | dairy | 8.43 | 4 | | yes | 79.7 | 80.5 | 273.5 |

Mean absolute error: least squares **17.3** units/week, naive **214.0**. TabFM's numbers come from the cloud run (see
the day's report). Full numbers in [`result.json`](result.json).
