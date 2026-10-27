-- name: daily_close (one statement per store; the store is also sent as a query tag)
SELECT department,
       CAST(SUM(units) AS INT)               AS units,
       CAST(SUM(net_sales) AS DECIMAL(12,2)) AS net_sales,
       CAST(COUNT(*) AS INT)                 AS lines
FROM {schema}.store_sales
WHERE store = '{store}'
GROUP BY department
ORDER BY department
