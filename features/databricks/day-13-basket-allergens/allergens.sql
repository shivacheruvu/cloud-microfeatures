-- name: databricks
-- Which allergens does each shopping basket and each aisle carry? collect_union (new in Databricks SQL 2026.35)
-- returns the distinct union of the elements of the arrays in each group, so the per-product allergen arrays roll up
-- to a basket or an aisle in one GROUP BY. The long-hand array_distinct(flatten(collect_list(...))) is computed next
-- to it as a self-check. collect_union gives no order guarantee, so results are array_sort-ed and sent as JSON.
-- {products} / {lines} are inline VALUES lists built from data/*.csv. Read-only: nothing is created.
WITH products AS (
  SELECT sku, aisle, allergens FROM VALUES
{products}
  AS t(sku, aisle, allergens)
), lines AS (
  SELECT basket_id, store, sku, qty FROM VALUES
{lines}
  AS t(basket_id, store, sku, qty)
), rolled AS (
  SELECT 'basket' AS level, l.basket_id AS group_key,
         collect_union(p.allergens) AS merged,
         array_distinct(flatten(collect_list(p.allergens))) AS long_hand
  FROM lines l JOIN products p USING (sku)
  GROUP BY l.basket_id
  UNION ALL
  SELECT 'aisle', aisle, collect_union(allergens), array_distinct(flatten(collect_list(allergens)))
  FROM products
  GROUP BY aisle
)
SELECT level, group_key,
       to_json(array_sort(merged)) AS allergens,
       size(merged) AS n_allergens,
       NOT arrays_overlap(merged, array('peanuts', 'tree_nuts')) AS nut_free,
       array_sort(merged) = array_sort(long_hand) AS matches_flatten
FROM rolled
ORDER BY level, group_key
