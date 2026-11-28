-- Day 53 Databricks: shelf-price history on two time axes with bitemporal AUTO CDC (STORED AS BITEMPORAL, Beta,
-- Lakeflow Spark Declarative Pipelines, Databricks Runtime 17.3+).
-- Business time (SEQUENCE BY effective_day): the November day a price takes effect on the shelf.
-- System time (SYSTEM SEQUENCE BY feed_batch): the price-feed batch that told us. A late batch can change what the
-- price was on a past day without changing that day; the old belief is closed (__SYSTEM_END_AT), never deleted.
-- The engine adds __START_AT / __END_AT (business time) and __SYSTEM_START_AT / __SYSTEM_END_AT (system time).
-- {source} is the price_feed Delta table outside the pipeline (append-only, one row per price change).
CREATE OR REFRESH STREAMING TABLE shelf_prices
COMMENT 'Shelf price per SKU on two axes: effective day (business) and feed batch (system)';

CREATE FLOW shelf_price_flow AS AUTO CDC INTO shelf_prices
FROM STREAM({source})
KEYS (sku)
SEQUENCE BY effective_day
SYSTEM SEQUENCE BY feed_batch
STORED AS BITEMPORAL;
