-- Is oat milk growing, or just noisy? ML.TREND (Preview, 2026-08-20): no model to create, one table-valued function
-- call returns each day's trend component per store (id_cols), plus a 14-day trend forecast (horizon).
WITH daily AS (
  SELECT sale_date, store, units
  FROM UNNEST([
{values}
  ])
)
SELECT *
FROM ML.TREND(
  TABLE daily,
  data_col => 'units',
  timestamp_col => 'sale_date',
  id_cols => ['store'],
  horizon => 14
)
ORDER BY store, sale_date
