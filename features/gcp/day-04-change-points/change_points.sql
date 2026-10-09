-- When did oat-milk sales shift, per store? ML.DETECT_CHANGE_POINTS (Preview, 2026-08-20): no model to create,
-- one table-valued function call over the daily series, one series per store (id_cols).
WITH daily AS (
  SELECT sale_date, store, units
  FROM UNNEST([
{values}
  ])
)
SELECT *
FROM ML.DETECT_CHANGE_POINTS(
  TABLE daily,
  data_col => 'units',
  timestamp_col => 'sale_date',
  id_cols => ['store']
)
ORDER BY store, begin_timestamp
