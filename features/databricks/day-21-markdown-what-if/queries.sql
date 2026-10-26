-- by_department: one row per department for a markdown level bound at query time ({markdown} is a literal)
SELECT department, {markdown} AS markdown, units, net_sales, margin, lines_below_cost FROM (
  SELECT department,
         MEASURE(units) AS units,
         MEASURE(net_sales) AS net_sales,
         MEASURE(margin) AS margin,
         MEASURE(lines_below_cost) AS lines_below_cost
  FROM {schema}.markdown_metrics(markdown => {markdown})
  GROUP BY ALL
) ORDER BY department;
-- default: no argument, so the parameter's default (0 = shelf price) applies
SELECT 'all' AS department, 0.0 AS markdown, units, net_sales, margin, lines_below_cost FROM (
  SELECT MEASURE(units) AS units,
         MEASURE(net_sales) AS net_sales,
         MEASURE(margin) AS margin,
         MEASURE(lines_below_cost) AS lines_below_cost
  FROM {schema}.markdown_metrics
)
