-- Day 15 (Google Cloud): one freshness check for every feed, written once. A table-valued function with an
-- ANY TABLE parameter (GA 2026-08-11) accepts a table of any shape, as long as it has the columns the body uses
-- (store, event_ts, loaded_ts). The three feeds have different columns; the same function checks all of them.
-- One multi-statement query in a short-lived dataset (cloud.sh deletes it; tables also expire after an hour).
-- First version used CREATE TEMP TABLE FUNCTION (shown on the docs page), but BigQuery answered "CREATE TEMPORARY
-- TABLE FUNCTION statements are not currently supported" (2026-10-19), so the function and tables are persistent.
-- {scans} / {deliveries} / {price_tags} are inline arrays of STRUCTs built from data/*.csv.
CREATE OR REPLACE TABLE `{ds}.scans` AS
SELECT * FROM UNNEST([
{scans}
]);

CREATE OR REPLACE TABLE `{ds}.deliveries` AS
SELECT * FROM UNNEST([
{deliveries}
]);

CREATE OR REPLACE TABLE `{ds}.price_tags` AS
SELECT * FROM UNNEST([
{price_tags}
]);

CREATE OR REPLACE TABLE FUNCTION `{ds}.late_rows`(input_table ANY TABLE, feed_name STRING, sla_minutes INT64) AS (
  SELECT
    feed_name AS feed,
    store,
    COUNT(*) AS n_rows,
    COUNTIF(TIMESTAMP_DIFF(loaded_ts, event_ts, MINUTE) > sla_minutes) AS late_rows,
    MAX(TIMESTAMP_DIFF(loaded_ts, event_ts, MINUTE)) AS max_delay_min
  FROM input_table
  GROUP BY store
);

SELECT * FROM `{ds}.late_rows`(TABLE `{ds}.scans`, 'scans', 15)
UNION ALL
SELECT * FROM `{ds}.late_rows`(TABLE `{ds}.deliveries`, 'deliveries', 120)
UNION ALL
SELECT * FROM `{ds}.late_rows`(TABLE `{ds}.price_tags`, 'price_tags', 30)
ORDER BY feed, store;
