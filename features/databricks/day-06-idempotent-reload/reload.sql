-- name: replace_using
-- Re-load a corrected export: delete target rows whose (store, scan_date) equals a row in the batch, then insert the batch.
-- Plain equality: a NULL scan_date never matches, so undated rows are appended again on every run.
INSERT INTO {schema}.{target}
  REPLACE USING (store, scan_date)
  SELECT * FROM {schema}.correction;

-- name: replace_on
-- The same re-load with a null-safe condition: <=> treats NULL = NULL as a match, so the undated row is replaced too.
INSERT INTO {schema}.{target} AS t
  REPLACE ON t.store <=> s.store AND t.scan_date <=> s.scan_date
  (SELECT * FROM {schema}.correction) AS s;

-- name: snapshot
SELECT store, CAST(scan_date AS STRING) AS scan_date, COUNT(*) AS lines, SUM(units) AS units,
       SUM(amount_cents) AS amount_cents
FROM {schema}.{target}
GROUP BY ALL
ORDER BY store, scan_date NULLS FIRST
