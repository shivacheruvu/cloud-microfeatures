-- Forecast accuracy for three grocery items with TimesFM 3.0 (Preview, 2026-10-07) in AI.EVALUATE, side by side with
-- TimesFM 2.5. New with TimesFM 3.0: the model can be used in AI.EVALUATE, not only AI.FORECAST. Each call forecasts
-- the 14 held-out days from 16 weeks of history and scores the forecast against the actual sales, per item (id_cols).
-- No model or dataset is created.
WITH sales_history AS (
  SELECT * FROM UNNEST([
    {history}
  ])
), sales_actual AS (
  SELECT * FROM UNNEST([
    {actual}
  ])
)
{selects}
ORDER BY model, item
