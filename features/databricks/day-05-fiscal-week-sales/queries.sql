-- by_store: one row per store and fiscal week, every measure from the metric view
SELECT store, week_index, units_sold, units_prev_week, units_prior_4wk, units_to_date FROM (
  SELECT store, week_index,
         MEASURE(units_sold) AS units_sold,
         MEASURE(units_prev_week) AS units_prev_week,
         MEASURE(units_prior_4wk) AS units_prior_4wk,
         MEASURE(units_to_date) AS units_to_date
  FROM {schema}.weekly_sales_metrics
  GROUP BY ALL
) ORDER BY store, week_index;
-- all_stores: the same measures with no store, re-aggregated by the metric view
SELECT 'all' AS store, week_index, units_sold, units_prev_week, units_prior_4wk, units_to_date FROM (
  SELECT week_index,
         MEASURE(units_sold) AS units_sold,
         MEASURE(units_prev_week) AS units_prev_week,
         MEASURE(units_prior_4wk) AS units_prior_4wk,
         MEASURE(units_to_date) AS units_to_date
  FROM {schema}.weekly_sales_metrics
  GROUP BY ALL
) ORDER BY week_index;
