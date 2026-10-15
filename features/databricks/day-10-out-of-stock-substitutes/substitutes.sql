-- name: databricks
-- The two closest in-stock substitutes for every out-of-stock item, with the new NEAREST BY join (Databricks SQL
-- 2026.20). No ON clause: the ranking expression scores every (out-of-stock, in-stock) pair and EXACT NEAREST 2
-- keeps the two smallest per out-of-stock row. The score is an integer "mismatch" in points:
--   1000 for a different category, + 1 per cent of unit-price difference (cents per 100 g),
--   + 1 per 10 g of pack-size difference, + 50 if one is organic and the other isn't.
-- {products} is an inline VALUES list built from data/products.csv.
WITH products AS (
  SELECT * FROM VALUES
{products}
  AS t(sku, product, category, cents_per_100g, pack_g, organic, in_stock)
), oos AS (
  SELECT * FROM products WHERE in_stock = 0
), shelf AS (
  SELECT * FROM products WHERE in_stock = 1
), pairs AS (
  SELECT q.sku AS oos_sku, q.product AS oos_product, t.sku AS sub_sku, t.product AS sub_product,
         IF(q.category = t.category, 0, 1000) + abs(q.cents_per_100g - t.cents_per_100g)
           + abs(q.pack_g - t.pack_g) div 10 + 50 * abs(q.organic - t.organic) AS score
  FROM oos q
  INNER JOIN shelf t
    EXACT NEAREST 2 BY DISTANCE
      IF(q.category = t.category, 0, 1000) + abs(q.cents_per_100g - t.cents_per_100g)
        + abs(q.pack_g - t.pack_g) div 10 + 50 * abs(q.organic - t.organic)
)
SELECT oos_sku, oos_product,
       CAST(row_number() OVER (PARTITION BY oos_sku ORDER BY score, sub_sku) AS INT) AS rank,
       sub_sku, sub_product, CAST(score AS INT) AS score
FROM pairs
ORDER BY oos_sku, rank;

-- name: local
-- The same answer without NEAREST BY, the way it's written everywhere else (DuckDB here): score the whole cross
-- join, rank inside each out-of-stock item, keep rank <= 2.
WITH products AS (
  SELECT * FROM read_csv('{csv}', header = true)
), oos AS (
  SELECT * FROM products WHERE in_stock = 0
), shelf AS (
  SELECT * FROM products WHERE in_stock = 1
), pairs AS (
  SELECT q.sku AS oos_sku, q.product AS oos_product, t.sku AS sub_sku, t.product AS sub_product,
         CASE WHEN q.category = t.category THEN 0 ELSE 1000 END + abs(q.cents_per_100g - t.cents_per_100g)
           + abs(q.pack_g - t.pack_g) // 10 + 50 * abs(q.organic - t.organic) AS score
  FROM oos q CROSS JOIN shelf t
)
SELECT oos_sku, oos_product,
       CAST(row_number() OVER (PARTITION BY oos_sku ORDER BY score, sub_sku) AS INT) AS rank,
       sub_sku, sub_product, CAST(score AS INT) AS score
FROM pairs
QUALIFY rank <= 2
ORDER BY oos_sku, rank
