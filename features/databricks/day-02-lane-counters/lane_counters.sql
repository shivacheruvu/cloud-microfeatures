-- Lane counters: how many items did each checkout lane scan today?
-- Each lane's scanner reports a cumulative counter every 15 minutes, and the counter restarts near zero
-- when the scanner reboots. New in Databricks SQL (2026-09-14): counter_diff(value) OVER (... ORDER BY t)
-- returns the increase since the previous reading and NULL on the first reading and on a reset, so
-- reboots don't show up as huge negative (or, with MAX - MIN, wildly wrong) totals.
WITH readings AS (
  SELECT lane, CAST(read_at AS TIMESTAMP) AS read_at, CAST(scan_counter AS BIGINT) AS scan_counter
  FROM VALUES
{values}
  AS t(lane, read_at, scan_counter)
),
deltas AS (
  SELECT lane, read_at, scan_counter,
         counter_diff(scan_counter) OVER (PARTITION BY lane ORDER BY read_at) AS items
  FROM readings
)
SELECT
  lane,
  COUNT(*) AS readings,
  SUM(items) AS items_scanned,
  COUNT(*) - COUNT(items) - 1 AS resets,
  MAX(items) AS peak_items_15min,
  MAX(scan_counter) - MIN(scan_counter) AS naive_max_minus_min
FROM deltas
GROUP BY lane
ORDER BY lane
