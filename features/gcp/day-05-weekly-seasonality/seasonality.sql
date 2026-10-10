-- Which weekday sells the most bread, per store? ML.SEASONALITY (Preview, 2026-08-20): no model to create, one
-- table-valued function call returns each day's weekly seasonal component, one series per store (id_cols).
WITH daily AS (
  SELECT sale_date, store, units
  FROM UNNEST([
{values}
  ])
)
SELECT *
FROM ML.SEASONALITY(
  TABLE daily,
  data_col => 'units',
  timestamp_col => 'sale_date',
  id_cols => ['store'],
  seasonalities => ['WEEKLY']
)
ORDER BY store, sale_date
