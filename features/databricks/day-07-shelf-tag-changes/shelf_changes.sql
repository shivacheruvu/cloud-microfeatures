-- name: create
-- Row tracking on, change data feed property NOT set: automatic change data feed (GA, Databricks SQL 2026.36)
-- computes row-level changes at query time from row tracking.
CREATE TABLE {schema}.shelf_prices (store STRING, barcode STRING, product STRING, price_cents INT)
  TBLPROPERTIES ('delta.enableRowTracking' = 'true');

-- name: step1_load
-- Tags printed: the opening price list for both stores.
INSERT INTO {schema}.shelf_prices SELECT store, barcode, product, price_cents FROM {schema}.price_base;

-- name: step2_supplier_increase
-- The dairy supplier raises whole milk by 20 cents in every store.
UPDATE {schema}.shelf_prices SET price_cents = price_cents + 20 WHERE product = 'whole milk 1gal';

-- name: step3_discontinue
-- North drops oat milk.
DELETE FROM {schema}.shelf_prices WHERE store = 'north' AND product = 'oat milk 64oz';

-- name: step4_weekly_feed
-- The weekly price feed: only rows whose price really changed are updated, and new items are added.
MERGE INTO {schema}.shelf_prices AS t
USING {schema}.price_feed AS s
  ON t.store = s.store AND t.barcode = s.barcode
WHEN MATCHED AND t.price_cents <> s.price_cents THEN UPDATE SET price_cents = s.price_cents
WHEN NOT MATCHED THEN INSERT *;

-- name: changes
-- Databricks only: every row-level change since the table was created, no enableChangeDataFeed needed.
SELECT _commit_version, _change_type, store, barcode, product, price_cents
FROM table_changes('{schema}.shelf_prices', 0)
ORDER BY _commit_version, store, barcode, _change_type;

-- name: snapshot
SELECT store, barcode, product, price_cents FROM {schema}.shelf_prices ORDER BY store, barcode
