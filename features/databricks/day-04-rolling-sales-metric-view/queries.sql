-- by_store: one row per store and day, every measure from the metric view
SELECT store, sale_date, units_sold, units_prior_7d, open_days_prior_7d, units_mtd FROM (
  SELECT store, sale_date,
         MEASURE(units_sold) AS units_sold,
         MEASURE(units_prior_7d) AS units_prior_7d,
         MEASURE(open_days_prior_7d) AS open_days_prior_7d,
         MEASURE(units_mtd) AS units_mtd
  FROM {schema}.store_sales_metrics
  GROUP BY ALL
) ORDER BY store, sale_date;
-- all_stores: the same measures at a coarser grain (no store), re-aggregated by the metric view
SELECT 'all' AS store, sale_date, units_sold, units_prior_7d, open_days_prior_7d, units_mtd FROM (
  SELECT sale_date,
         MEASURE(units_sold) AS units_sold,
         MEASURE(units_prior_7d) AS units_prior_7d,
         MEASURE(open_days_prior_7d) AS open_days_prior_7d,
         MEASURE(units_mtd) AS units_mtd
  FROM {schema}.store_sales_metrics
  GROUP BY ALL
) ORDER BY sale_date;
