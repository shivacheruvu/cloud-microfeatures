-- Baking-aisle forecast for two stores: flour, butter and eggs forecast together in ONE AI.FORECAST call with
-- TimesFM 3.0 multi-target mode (target_cols with three columns; Preview, 2026-10-07). id_cols => ['store'] makes it
-- two series of three targets. The held-out 14 days are never sent. No model, dataset or table is created.
WITH sales AS (
  SELECT * FROM UNNEST([
    {sales}
  ])
)
SELECT
  store,
  CAST(day AS STRING) AS day,
  flour.value AS flour, flour.prediction_interval_lower_bound AS flour_lo, flour.prediction_interval_upper_bound AS flour_hi,
  butter.value AS butter, butter.prediction_interval_lower_bound AS butter_lo, butter.prediction_interval_upper_bound AS butter_hi,
  eggs.value AS eggs, eggs.prediction_interval_lower_bound AS eggs_lo, eggs.prediction_interval_upper_bound AS eggs_hi,
  ai_forecast_status
FROM AI.FORECAST(
  TABLE sales,
  target_cols => ['flour', 'butter', 'eggs'],
  timestamp_col => 'day',
  id_cols => ['store'],
  horizon => {horizon},
  confidence_level => 0.90,
  model => 'TimesFM 3.0'
)
ORDER BY store, day
