-- name: create_stock
-- Catalog commits ('delta.feature.catalogManaged') are required on every table a transaction writes.
CREATE TABLE {schema}.stock (sku STRING, product STRING, on_hand INT)
  TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

-- name: create_receipts
CREATE TABLE {schema}.receipts (delivery_id STRING, line INT, sku STRING, qty INT)
  TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

-- name: check_duplicate
-- Has this delivery been booked already? (a resent delivery note)
SELECT count(*) FROM {schema}.receipts WHERE delivery_id = '{delivery}';

-- name: check_lines
-- Bad lines in this delivery: a quantity that isn't positive, or a SKU the store doesn't stock.
SELECT count(*) FROM {schema}.receipts
WHERE delivery_id = '{delivery}' AND (qty <= 0 OR sku NOT IN (SELECT sku FROM {schema}.stock));

-- name: book_stock
-- Add the delivery's quantities to stock on hand.
MERGE INTO {schema}.stock AS t
USING (SELECT sku, sum(qty) AS qty FROM {schema}.receipts WHERE delivery_id = '{delivery}' GROUP BY sku) AS r
  ON t.sku = r.sku
WHEN MATCHED THEN UPDATE SET on_hand = t.on_hand + r.qty;

-- name: atomic
-- Databricks: one delivery = one BEGIN ATOMIC block (transactions GA 2026-10-14). The receipt lines are written
-- first; if a check fails, SIGNAL raises an error and the block rolls back, so neither table changes.
BEGIN ATOMIC
  IF ({check_duplicate}) > 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'rejected {delivery}: already booked';
  END IF;
  INSERT INTO {schema}.receipts VALUES {lines};
  IF ({check_lines}) > 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'rejected {delivery}: bad line (qty <= 0 or unknown SKU)';
  END IF;
  {book_stock};
END
