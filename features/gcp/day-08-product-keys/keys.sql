-- name: create
-- Product master with a surrogate key BigQuery generates on insert: an identity column (Preview, 2026-08-31).
-- {ds} is a short-lived dataset (default table expiration 1 hour) that cloud.sh deletes at the end.
CREATE TABLE `{ds}.products` (
  product_key INT64 GENERATED ALWAYS AS IDENTITY (START WITH 1000 INCREMENT BY 10),
  barcode STRING NOT NULL,
  name STRING
);

-- name: load
-- The opening product list: product_key is left out, so BigQuery fills it in.
INSERT `{ds}.products` (barcode, name)
SELECT barcode, name FROM UNNEST([{products}]);

-- name: add_new
-- The weekly vendor feed: only barcodes not already in the master get a new key.
INSERT `{ds}.products` (barcode, name)
SELECT n.barcode, n.name FROM UNNEST([{new_products}]) AS n
WHERE NOT EXISTS (SELECT 1 FROM `{ds}.products` AS p WHERE p.barcode = n.barcode);

-- name: explicit_key
-- Must fail: GENERATED ALWAYS does not accept a key written by hand.
INSERT `{ds}.products` (product_key, barcode, name) VALUES (1, '0000000000000', 'hand-keyed');

-- name: products
SELECT product_key, barcode, name FROM `{ds}.products` ORDER BY barcode;

-- name: identity_info
SELECT column_name, is_identity, identity_generation, identity_start, identity_increment
FROM `{ds}.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'products' AND column_name = 'product_key';

-- name: scan_facts
-- Scan events carry barcodes, and the fact table stores the generated key instead.
SELECT p.product_key, p.name, SUM(s.units) AS units, COUNT(*) AS scans
FROM UNNEST([{scans}]) AS s
JOIN `{ds}.products` AS p ON p.barcode = s.barcode
GROUP BY p.product_key, p.name
ORDER BY p.name
