-- The same measures written by hand in DuckDB, used to check the metric view. {markdown} is a literal.
SELECT department, {markdown} AS markdown, SUM(units) AS units,
       SUM(ROUND((1 - {markdown}) * shelf_price, 2) * units) AS net_sales,
       SUM((ROUND((1 - {markdown}) * shelf_price, 2) - unit_cost) * units) AS margin,
       COUNT_IF(ROUND((1 - {markdown}) * shelf_price, 2) < unit_cost) AS lines_below_cost
FROM week_sales
GROUP BY department
ORDER BY department
