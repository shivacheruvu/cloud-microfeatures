# Day 52 · Google Cloud · Baking-aisle forecast: flour, butter and eggs in one TimesFM 3.0 call

**New platform feature:** **multi-target forecasting with TimesFM 3.0** in BigQuery's `AI.FORECAST`: several
`target_cols` forecast together in one call (**Preview, 2026-10-07**,
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes);
[announcement](https://cloud.google.com/blog/products/data-analytics/bigquery-timesfm-3-0-with-multivariate-time-series-forecasting),
[tutorial](https://docs.cloud.google.com/bigquery/docs/timesfm-multivariate-single-time-series-forecasting-tutorial)).
Day 10 used TimesFM 3.0 for one target plus a promo covariate; today is the other half of that release: **three
targets at once**, each returned as its own `STRUCT(value, prediction_interval_lower_bound,
prediction_interval_upper_bound)` column, combined with `id_cols` for two stores.
Syntax follows Google's [`bigquery-ai-ml` skill](https://github.com/google/skills/tree/main/skills/cloud/bigquery-ai-ml)
(`references/ai_forecast.md`, section 2: multivariate mode needs `model => 'TimesFM 3.0'` and two or more targets).

## What it does
16 weeks of daily sales (2026-07-06 to 2026-10-25) of 5 lb flour, 1 lb butter and a dozen eggs at two stores
(north, and south at 70% of north's volume): weekend baking peaks, a fall-baking upward drift for flour and butter,
a ±5% store-traffic swing shared by all three items each day and ±4% item noise. The next 14 days are held out.
One query forecasts all six series:

```sql
SELECT store, day, flour.value AS flour, butter.value AS butter, eggs.value AS eggs, ...
FROM AI.FORECAST(
  TABLE sales,
  target_cols => ['flour', 'butter', 'eggs'],
  timestamp_col => 'day', id_cols => ['store'],
  horizon => 14, confidence_level => 0.90, model => 'TimesFM 3.0')
```

A second query forecasts the same six series the pre-3.0 way (long format via `UNPIVOT`, `data_col => 'units'`,
`id_cols => ['store', 'item']`, TimesFM 2.5) so the report can compare the two. No model, dataset or table is created.

## Why it matters
A store orders a whole aisle, not one SKU. Baking items move together (Saturday flour, butter and eggs go in the same
basket), and before TimesFM 3.0 each one needed its own series: reshape to long format, forecast, reshape back. With
`target_cols` the wide sales table goes in as it is and comes back wide, with an interval per item, ready for an
order sheet.

## How to run
```bash
python features/gcp/day-52-baking-aisle-multi-target/forecast.py --local           # baselines; writes result.json
python features/gcp/day-52-baking-aisle-multi-target/forecast.py --sql             # the multi-target query
python features/gcp/day-52-baking-aisle-multi-target/forecast.py --sql-univariate  # the TimesFM 2.5 comparison
pytest -q tests/test_day52_gcp_timesfm3_multi_target.py
```
`cloud.sh` runs both queries in GitHub Actions (keyless). It **fails** if any of the 84 forecasts (2 stores x 3 items
x 14 days) is missing or not positive, an interval doesn't hold its own forecast, `ai_forecast_status` is not empty,
or the WAPE is over 1.25x the seasonal naive one. It **warns** if the multi-target forecast doesn't beat seasonal
naive, if the 90% intervals hold under 70% of actuals, or if the univariate comparison fails. TimesFM isn't
bit-for-bit reproducible, so the check compares errors, not exact values.

## Cost
Two `AI.FORECAST` queries over a few KB of inlined data, each capped at 20 MiB billed (free tier). Nothing is created,
so there's nothing to clean up.

## Result
Local baselines on the 84 held-out values (WAPE = sum of absolute errors / sum of actual units):

| Forecast | All | Flour | Butter | Eggs |
|---|---|---|---|---|
| Seasonal naive (same weekday last week) | 5.69% | 5.01% | 5.00% | 6.34% |
| 4-week weekday mean | 4.00% | 5.56% | 3.96% | 3.42% |
| **TimesFM 3.0 multi-target, one call (cloud)** | **4.05%** | 4.24% | **3.50%** | 4.26% |
| TimesFM 2.5 univariate, six series (cloud) | 4.31% | 4.30% | 3.90% | 4.53% |

Cloud run of 2026-11-25 (merge of PR #168): all 84 forecasts present, positive and inside their own intervals, every
`ai_forecast_status` empty, and the 90% intervals held **94%** of the actual values. The single multi-target call
beat seasonal naive by 1.6 points and the six separate univariate TimesFM 2.5 forecasts by 0.26 points; it is level
with the 4-week weekday mean (4.00%), which suits this synthetic data's steady weekly pattern.
