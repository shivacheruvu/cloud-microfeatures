-- Promo-aware 14-day forecast of daily strawberry sales with TimesFM 3.0 (Preview, 2026-10-07) in AI.FORECAST.
-- New in TimesFM 3.0: covariates. on_promo is a FUTURE covariate: known for history and for the 14 horizon rows,
-- whose units are NULL (the held-out sales are never sent). No model is created; nothing to clean up.
WITH sales AS (
  SELECT * FROM UNNEST([
    {sales}
  ])
)
SELECT *
FROM AI.FORECAST(
  TABLE sales,
  model => 'TimesFM 3.0',
  timestamp_col => 'day',
  target_cols => ['units'],
  future_covariate_cols => ['on_promo'],
  horizon => {horizon},
  confidence_level => 0.90
)
