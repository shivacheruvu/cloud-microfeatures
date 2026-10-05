-- Scan rush hours: when do shoppers scan, and how healthy is what they scan?
-- New in Databricks SQL (2026-09-08): time_bucket(width, ts, origin) aligns timestamps to fixed-width
-- buckets that start at a chosen origin. Here: 30-minute slots aligned to the store's 07:15 opening,
-- which date_trunc can't express.
WITH scans AS (
  SELECT CAST(scanned_at AS TIMESTAMP) AS scanned_at, barcode, category, CAST(health_score AS INT) AS health_score
  FROM VALUES
{values}
  AS t(scanned_at, barcode, category, health_score)
)
SELECT
  time_bucket(INTERVAL '30' MINUTE, scanned_at, TIMESTAMP '1970-01-01 00:15:00') AS slot_start,
  COUNT(*) AS scans,
  ROUND(AVG(health_score), 1) AS avg_health_score,
  ROUND(100.0 * SUM(CASE WHEN health_score >= 70 THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_healthy
FROM scans
GROUP BY 1
ORDER BY 1
