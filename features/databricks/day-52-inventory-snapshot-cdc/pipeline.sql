-- Day 52 Databricks: on-hand history from nightly whole-store inventory snapshots with AUTO CDC ... FROM SNAPSHOT
-- (SQL snapshot CDC in Lakeflow Spark Declarative Pipelines, Databricks Runtime 18.3+).
-- Each snapshot_version is the store's COMPLETE on-hand count. The engine diffs each version with the previous
-- committed one: a changed count closes the old row and opens a new one, an unchanged SKU writes nothing, and a SKU
-- missing from the snapshot is a delete (its row is closed). WITH VERSION hands out the next unprocessed version,
-- one at a time, until none is left, so one pipeline update can work through several new snapshots in order.
-- snapshot_version must NOT be in the snapshot query's output: it changes every night, so every row would look
-- changed. {source} is the inventory_snapshots Delta table outside the pipeline.
CREATE OR REFRESH STREAMING TABLE inventory_history
COMMENT 'On-hand count history per SKU (SCD type 2); __START_AT / __END_AT are snapshot versions'
FLOW AUTO CDC
FROM SNAPSHOT (
  SELECT sku, on_hand
  FROM {source}
  WHERE snapshot_version = (SELECT version FROM current_snapshot_version())
)
WITH VERSION (
  SELECT version
  FROM (SELECT DISTINCT snapshot_version AS version FROM {source})
  WHERE NOT EXISTS (SELECT 1 FROM last_snapshot_version())
     OR version > (SELECT version FROM last_snapshot_version())
  ORDER BY version
  LIMIT 1
)
KEYS (sku)
STORED AS SCD TYPE 2;
