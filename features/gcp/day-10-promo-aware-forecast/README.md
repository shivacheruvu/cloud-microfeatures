# Day 10 · Google Cloud · Promo-aware strawberry forecast: TimesFM 3.0 with a future covariate

**New platform feature:** the **TimesFM 3.0** model in BigQuery (**Preview, 2026-10-07**,
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)), used through `AI.FORECAST`
with `model => 'TimesFM 3.0'`. What's new against TimesFM 2.x: multivariate forecasting and **covariates**, both
past-only and past-and-future
([announcement](https://cloud.google.com/blog/products/data-analytics/bigquery-timesfm-3-0-with-multivariate-time-series-forecasting),
[tutorial](https://docs.cloud.google.com/bigquery/docs/timesfm-multivariate-single-time-series-forecasting-tutorial)).

## What it does
12 weeks of daily sales of 1 lb strawberries at one store (weekday pattern, a slow upward drift, ±6% noise, and
11 promo days that sell about 1.6x). The next 14 days are held out; 3 of them are promo days on the calendar.
One BigQuery query sends the history plus 14 **horizon rows** whose `units` are NULL but whose `on_promo` is known,
and asks TimesFM 3.0 for the 14-day forecast with `on_promo` as a **future covariate**:

```sql
SELECT * FROM AI.FORECAST(
  TABLE sales,
  model => 'TimesFM 3.0', timestamp_col => 'day', target_cols => ['units'],
  future_covariate_cols => ['on_promo'], horizon => 14, confidence_level => 0.90)
```

A second query runs the same forecast without the promo calendar (univariate TimesFM 3.0) to show what the
covariate adds. No model is trained or stored, and no dataset is created.

## Why it matters
Grocery demand is driven by things you know in advance: promos, holidays, price changes. A plain time-series model
only sees the past, so it can't know next Tuesday is a promo day and misses exactly the days that matter for ordering
and staffing. With TimesFM 3.0 the planned promo calendar goes straight into the zero-shot forecast, in SQL.

## How to run
```bash
python features/gcp/day-10-promo-aware-forecast/forecast.py --local       # baselines; writes result.json
python features/gcp/day-10-promo-aware-forecast/forecast.py --sql         # the BigQuery query (with promos)
python features/gcp/day-10-promo-aware-forecast/forecast.py --sql-plain   # the univariate query
pytest -q tests/test_day10_gcp_timesfm3_covariates.py
```
`cloud.sh` runs both queries in GitHub Actions (keyless) and checks the forecast against the held-out 14 days:
every day needs a positive forecast and the TimesFM-with-promos MAE must beat the seasonal naive forecast. It warns
if the covariate doesn't beat univariate TimesFM, or if TimesFM is over 1.5x the local promo-aware baseline.

## Cost
Two `AI.FORECAST` queries over a few KB of inlined data, each capped at 20 MiB billed (free tier). Nothing is
created, so there's nothing to clean up.

## Result
Local baselines on the 14 held-out days (units/day):

| | MAE, all 14 days | MAE, 3 promo days |
|---|---|---|
| Seasonal naive (same weekday last week) | 19.9 | 72.9 |
| Promo-aware baseline (4-week weekday mean x 1.58 promo lift) | **6.9** | **10.8** |
| Univariate TimesFM 3.0, no promo calendar (cloud) | 18.6 | 68.2 |
| **TimesFM 3.0 with the promo calendar (cloud)** | **8.6** | **17.4** |

| Day | Promo | Actual | Seasonal naive | Promo-aware baseline | TimesFM 3.0 + promos |
|---|---|---|---|---|---|
| Tue 09-29 | yes | 185.5 | 112.4 | 171.8 | 167.1 |
| Wed 09-30 | yes | 185.3 | 108.7 | 170.7 | 173.7 |
| Thu 10-08 | yes | 194.9 | 125.9 | 190.7 | 172.8 |

**Cloud run (2026-10-14):** both `AI.FORECAST` queries ran on BigQuery. The promo calendar more than halves
TimesFM's error (MAE 18.6 → **8.6** units/day) and cuts it by three quarters on promo days (68.2 → 17.4): TimesFM
forecast 167, 174 and 173 units for the three promo days (actual 186, 185, 195), against ~110-125 without the
covariate. The simple promo-aware baseline (6.9) is still a bit better on this clean synthetic series, which has
exactly the structure that baseline assumes.

Full numbers in [`result.json`](result.json).
