-- The same forecast without the promo calendar (univariate TimesFM 3.0), to see what the covariate adds.
WITH sales AS (
  SELECT day, units FROM UNNEST([
    {sales}
  ])
)
SELECT *
FROM AI.FORECAST(
  TABLE sales,
  model => 'TimesFM 3.0',
  timestamp_col => 'day',
  target_cols => ['units'],
  horizon => {horizon},
  confidence_level => 0.90
)
