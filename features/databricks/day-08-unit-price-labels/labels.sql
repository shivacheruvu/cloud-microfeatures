-- name: labels
-- Unit price per 100 g / 100 ml for every shelf label, truncated (new `truncate`, Databricks SQL 2026.38) and,
-- for comparison, rounded. The promo saving is negative, so truncation goes toward zero: the label never
-- overstates the unit price or the saving. {products} is an inline VALUES list built from data/products.csv.
WITH products AS (
  SELECT * FROM VALUES
{products}
  AS t(barcode, product, price_cents, promo_cents, size, unit)
), unit_prices AS (
  SELECT barcode, product,
         CAST(price_cents AS DECIMAL(10, 0)) / CAST(size AS DECIMAL(10, 0)) AS unit_price,
         CAST(promo_cents - price_cents AS DECIMAL(10, 0)) / CAST(size AS DECIMAL(10, 0)) AS promo_saving
  FROM products
)
SELECT barcode, product,
       CAST(unit_price AS STRING)                 AS unit_price,
       CAST(truncate(unit_price, 2) AS STRING)    AS unit_label,
       CAST(round(unit_price, 2) AS STRING)       AS unit_rounded,
       CAST(truncate(promo_saving, 2) AS STRING)  AS saving_label
FROM unit_prices
ORDER BY barcode;

-- name: baskets
-- A cashier's "void last N scans": the new `trim_array` (Databricks SQL 2026.38) drops N elements from the end of
-- the scan list, and the receipt total is summed over what is left.
WITH products AS (
  SELECT * FROM VALUES
{products}
  AS t(barcode, product, price_cents, promo_cents, size, unit)
), price_map AS (
  SELECT map_from_arrays(collect_list(barcode), collect_list(price_cents)) AS m FROM products
), baskets AS (
  SELECT * FROM VALUES
{baskets}
  AS t(basket, scans, voids)
)
SELECT basket,
       to_json(trim_array(scans, voids))                                         AS kept,
       size(trim_array(scans, voids))                                            AS items,
       aggregate(trim_array(scans, voids), 0, (acc, b) -> acc + price_map.m[b])  AS total_cents
FROM baskets CROSS JOIN price_map
ORDER BY basket
