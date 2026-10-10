-- The same measures written by hand with integer RANGE window functions (DuckDB), used to check the metric view.
-- {grain} is "store" (by_store) or "'all'" (all_stores).
WITH weekly AS (
  SELECT {grain} AS store, week_index, SUM(units) AS units_sold FROM weekly_units GROUP BY ALL
)
SELECT store, week_index, units_sold,
       SUM(units_sold) OVER (PARTITION BY store ORDER BY week_index
                             RANGE BETWEEN 1 PRECEDING AND 1 PRECEDING) AS units_prev_week,
       SUM(units_sold) OVER (PARTITION BY store ORDER BY week_index
                             RANGE BETWEEN 4 PRECEDING AND 1 PRECEDING) AS units_prior_4wk,
       SUM(units_sold) OVER (PARTITION BY store ORDER BY week_index
                             RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS units_to_date
FROM weekly
ORDER BY store, week_index
