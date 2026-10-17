# Day 12 · Google Cloud · Forecast accuracy with TimesFM 3.0 in `AI.EVALUATE`

**New platform feature:** **TimesFM 3.0** in BigQuery (**Preview, 2026-10-07**,
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)). The release note says the new
model works with `AI.FORECAST`, **`AI.EVALUATE`** ("to perform univariate time series evaluation") and
`AI.DETECT_ANOMALIES`. Day 10 forecast with it and day 11 detected anomalies; this day scores it.
Reference: [AI.EVALUATE](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-evaluate)
(the reference page still lists only TimesFM 2.0 / 2.5 as `model` values; the release note adds 3.0).

## What it does
18 weeks of daily sales for three items at one store: bananas (flat), milk (slowly declining) and oat milk (growing
1.5% a week), each with a weekday pattern and ±6% noise. The first 16 weeks are history; the last 14 days are held
out. One BigQuery query scores both TimesFM versions on all three items:

```sql
SELECT 'TimesFM 3.0' AS model, * FROM AI.EVALUATE(
  TABLE sales_history, TABLE sales_actual,
  data_col => 'units', timestamp_col => 'day', id_cols => ['item'],
  model => 'TimesFM 3.0', horizon => 14)
UNION ALL
SELECT 'TimesFM 2.5' AS model, * FROM AI.EVALUATE(... model => 'TimesFM 2.5' ...)
```

Each row has MAE, MSE, RMSE, MAPE, sMAPE and MASE for one item and model. The runner puts them next to the
seasonal-naive baseline ("same weekday last week"), computed locally in pure Python. No model is trained or stored
and no dataset is created.

## Why it matters
Before trusting a zero-shot forecast for ordering, you want to know how wrong it is on your own items, and whether
it beats the trivial "same as last week" rule. `AI.EVALUATE` does the backtest in one SQL call per model, so a new
model version (3.0 here) can be checked against the old one (2.5) on the same items before switching.

## How to run
```bash
python features/gcp/day-12-forecast-accuracy/evaluate.py --local     # seasonal-naive baseline; writes result.json
python features/gcp/day-12-forecast-accuracy/evaluate.py --sql       # print the BigQuery query
pytest -q tests/test_day12_gcp_timesfm3_evaluate.py
```
`cloud.sh` runs the query in GitHub Actions (keyless). It fails if any (model, item) row is missing, has an
`ai_evaluate_status` message or a non-finite MAE/RMSE, or if an MAE is above the larger of 3x the seasonal-naive MAE
and 10% of mean sales (a sign the wrong thing was scored). It warns when TimesFM 3.0 loses to seasonal naive on an
item. If the query is rejected, it reruns with TimesFM 2.5 only, for diagnosis (the run still fails).

## Cost
One query over a few KB of inlined data, capped at 20 MiB billed (well inside the free tier). Nothing is created.

## Result
Seasonal-naive baseline on the 14 held-out days:

| Item | Mean daily units | MAE | RMSE | MAPE |
|---|---|---|---|---|
| bananas (lb) | 199.2 | 9.93 | 11.29 | 5.01% |
| milk 1 gal | 127.3 | 3.86 | 5.14 | 3.04% |
| oat milk 64 oz | 33.1 | 1.00 | 1.31 | 2.84% |

TimesFM 3.0 and 2.5 metrics come from the cloud run (see the day 12 report). Full numbers in
[`result.json`](result.json).
