-- What moves daily ice cream sales in each store: the weather, the promotion, or foot traffic?
-- ML.CORRELATION (Preview, 2026-09-10) correlates a target column with several columns at once, for every segment of
-- the dimension columns plus the overall rollup (segment = []), with no model to train. It runs twice here, PEARSON
-- (linear) and SPEARMAN (rank), on the same inlined data. Nothing is created.
WITH days AS (
  SELECT * FROM UNNEST([
    {days}
  ])
)
SELECT 'PEARSON' AS method, IF(ARRAY_LENGTH(segment) = 0, 'ALL', store) AS store, corr_col, correlation, segment_size
FROM ML.CORRELATION(
  (SELECT store, ice_cream_revenue, max_temp_c, promo_pct, foot_traffic FROM days),
  target_col => 'ice_cream_revenue',
  target_correlation_cols => ['max_temp_c', 'promo_pct', 'foot_traffic'],
  dimension_cols => ['store'],
  method => 'PEARSON')
UNION ALL
SELECT 'SPEARMAN', IF(ARRAY_LENGTH(segment) = 0, 'ALL', store), corr_col, correlation, segment_size
FROM ML.CORRELATION(
  (SELECT store, ice_cream_revenue, max_temp_c, promo_pct, foot_traffic FROM days),
  target_col => 'ice_cream_revenue',
  target_correlation_cols => ['max_temp_c', 'promo_pct', 'foot_traffic'],
  dimension_cols => ['store'],
  method => 'SPEARMAN')
ORDER BY method, store, corr_col
