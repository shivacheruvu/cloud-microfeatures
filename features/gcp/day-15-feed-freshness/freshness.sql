-- Day 15 (Google Cloud): one freshness check for every feed, written once. A table-valued function with an
-- ANY TABLE parameter (GA 2026-08-11) accepts a table of any shape, as long as it has the columns the body uses
-- (store, event_ts, loaded_ts). The three feeds have different columns; the same function checks all of them.
-- One multi-statement query: temporary tables and a temporary table function only, so nothing is left behind.
-- {scans} / {deliveries} / {price_tags} are inline arrays of STRUCTs built from data/*.csv.
CREATE TEMP TABLE scans AS
SELECT * FROM UNNEST([
{scans}
]);

CREATE TEMP TABLE deliveries AS
SELECT * FROM UNNEST([
{deliveries}
]);

CREATE TEMP TABLE price_tags AS
SELECT * FROM UNNEST([
{price_tags}
]);

CREATE TEMP TABLE FUNCTION late_rows(input_table ANY TABLE, feed_name STRING, sla_minutes INT64) AS (
  SELECT
    feed_name AS feed,
    store,
    COUNT(*) AS n_rows,
    COUNTIF(TIMESTAMP_DIFF(loaded_ts, event_ts, MINUTE) > sla_minutes) AS late_rows,
    MAX(TIMESTAMP_DIFF(loaded_ts, event_ts, MINUTE)) AS max_delay_min
  FROM input_table
  GROUP BY store
);

SELECT * FROM late_rows(TABLE scans, 'scans', 15)
UNION ALL
SELECT * FROM late_rows(TABLE deliveries, 'deliveries', 120)
UNION ALL
SELECT * FROM late_rows(TABLE price_tags, 'price_tags', 30)
ORDER BY feed, store;
