-- Day 51 Databricks: shelf prices kept current from store price files with a REPLACE USING flow
-- (Spark Declarative Pipelines, Beta, Databricks Runtime 18.2+, classic or serverless).
-- Each delivery is a store's FULL price file. A newer file_version for a store atomically replaces every row that
-- store had (SKUs dropped from the file disappear); a late, older file is ignored because SEQUENCE BY orders it first.
-- {source} is the price_drops Delta table outside the pipeline, read as a stream.
CREATE STREAMING TABLE shelf_prices
COMMENT 'Current shelf price list per store: the rows of the newest price file each store sent'
FLOW REPLACE USING (store_id) SEQUENCE BY file_version BY NAME
SELECT store_id, file_version, sku, price_cents
FROM STREAM({source});
