-- The same three measures written by hand with window functions (DuckDB), used to check the metric view.
-- {grain} is "store" (by_store) or "'all'" (all_stores).
WITH daily AS (
  SELECT {grain} AS store, sale_date, SUM(units) AS units_sold FROM daily_units GROUP BY ALL
)
SELECT store, sale_date, units_sold,
       SUM(units_sold) OVER (PARTITION BY store ORDER BY sale_date
                             RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING) AS units_prior_7d,
       COUNT(*) OVER (PARTITION BY store ORDER BY sale_date
                      RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING) AS open_days_prior_7d,
       SUM(units_sold) OVER (PARTITION BY store ORDER BY sale_date
                             RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS units_mtd
FROM daily
ORDER BY store, sale_date
