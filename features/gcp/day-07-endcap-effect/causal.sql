-- Did the endcap move sales? AI.CAUSAL_EFFECT (Preview, 2026-09-10): one table-valued function call fits ARIMA_PLUS to
-- each product's sales before the intervention, forecasts what would have happened after it, and tests the difference.
WITH daily AS (
  SELECT sale_date, product, units
  FROM UNNEST([
{values}
  ])
)
SELECT *
FROM AI.CAUSAL_EFFECT(
  TABLE daily,
  data_col => 'units',
  timestamp_col => 'sale_date',
  intervention_timestamp => '{intervention}',
  id_cols => ['product']
)
ORDER BY product
