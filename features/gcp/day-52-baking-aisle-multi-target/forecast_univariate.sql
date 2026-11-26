-- The same six series the old way: long format, one univariate AI.FORECAST per (store, item) with TimesFM 2.5
-- (data_col + id_cols). Used only to compare against the multi-target call. Nothing is created.
WITH sales AS (
  SELECT * FROM UNNEST([
    {sales}
  ])
),
long_sales AS (
  SELECT store, day, item, units
  FROM sales UNPIVOT (units FOR item IN (flour, butter, eggs))
)
SELECT
  store, item,
  CAST(DATE(forecast_timestamp) AS STRING) AS day,
  forecast_value AS value,
  prediction_interval_lower_bound AS lo, prediction_interval_upper_bound AS hi,
  ai_forecast_status
FROM AI.FORECAST(
  TABLE long_sales,
  data_col => 'units',
  timestamp_col => 'day',
  id_cols => ['store', 'item'],
  horizon => {horizon},
  confidence_level => 0.90,
  model => 'TimesFM 2.5'
)
ORDER BY store, item, day
