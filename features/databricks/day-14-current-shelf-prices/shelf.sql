-- name: databricks
-- What does each store charge today, and which items sell best in each aisle? QUALIFY (new in Databricks SQL
-- 2026.20) filters on window-function results in the same SELECT, so neither question needs a subquery:
--   * current price: keep the newest shelf-tag row per (store, sku), and the row count and the previous price come from
--     windows that are evaluated before QUALIFY filters, so they still see every price change
--   * top sellers: total units per aisle and sku, then keep RANK() <= 2 (ties kept). Databricks QUALIFY rejects
--     aggregate functions (RANK() OVER (ORDER BY SUM(qty))), so the totals come from a GROUP BY step first
-- {tags} / {sales} are inline VALUES lists built from data/*.csv. Read-only: nothing is created.
WITH tags AS (
  SELECT store, sku, CAST(effective_date AS DATE) AS effective_date, price_cents FROM VALUES
{tags}
  AS t(store, sku, effective_date, price_cents)
), sales AS (
  SELECT store, aisle, sku, qty FROM VALUES
{sales}
  AS t(store, aisle, sku, qty)
), current_price AS (
  SELECT 'price' AS kind, store AS grp, sku, price_cents AS value,
         CAST(effective_date AS STRING) AS detail,
         COUNT(*) OVER (PARTITION BY store, sku) AS n,
         LAG(price_cents) OVER (PARTITION BY store, sku ORDER BY effective_date) AS prev
  FROM tags
  QUALIFY ROW_NUMBER() OVER (PARTITION BY store, sku ORDER BY effective_date DESC) = 1
), units AS (
  SELECT aisle, sku, SUM(qty) AS units, COUNT(DISTINCT store) AS stores
  FROM sales
  GROUP BY aisle, sku
), top_sellers AS (
  SELECT 'top_seller' AS kind, aisle AS grp, sku, units AS value,
         CAST(RANK() OVER (PARTITION BY aisle ORDER BY units DESC) AS STRING) AS detail,
         stores AS n,
         CAST(NULL AS INT) AS prev
  FROM units
  QUALIFY RANK() OVER (PARTITION BY aisle ORDER BY units DESC) <= 2
)
SELECT kind, grp, sku, value, detail, n, prev FROM current_price
UNION ALL
SELECT kind, grp, sku, value, detail, n, prev FROM top_sellers
ORDER BY kind, grp, sku
