-- Barcode audit: which checkout lanes produce scans that can't be real GTINs?
-- gtin_status is the Unity Catalog Python UDF from gtin_status.sql, called once per scan.
WITH scans AS (
  SELECT CAST(scanned_at AS TIMESTAMP) AS scanned_at, lane, barcode
  FROM VALUES
{values}
  AS t(scanned_at, lane, barcode)
),
checked AS (
  SELECT lane, barcode, {schema}.gtin_status(barcode) AS status FROM scans
)
SELECT
  lane,
  COUNT(*) AS scans,
  COUNT_IF(status = 'valid') AS valid,
  COUNT_IF(status = 'bad_check_digit') AS bad_check_digit,
  COUNT_IF(status = 'bad_length') AS bad_length,
  COUNT_IF(status = 'not_numeric') AS not_numeric,
  COUNT(DISTINCT CASE WHEN status <> 'valid' THEN barcode END) AS distinct_bad_codes
FROM checked
GROUP BY lane
ORDER BY lane
