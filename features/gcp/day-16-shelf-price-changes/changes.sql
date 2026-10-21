-- name: setup
-- A week of shelf-price edits on one table created with enable_change_history = TRUE, one statement per commit
-- (a script, not a transaction: CHANGES can't read tables with multi-statement transactions in the window).
CREATE TABLE `{ds}.shelf_prices` (sku STRING, name STRING, category STRING, price_cents INT64)
OPTIONS (enable_change_history = TRUE);
{steps}

-- name: changes
-- Every change since the table was created, numbered by commit. An UPDATE comes back as two rows: the old row
-- (DELETE, _CHANGE_IS_FOR_UPDATE = TRUE) and the new row (UPDATE).
SELECT DENSE_RANK() OVER (ORDER BY _CHANGE_TIMESTAMP) AS commit_no, sku, price_cents,
       _CHANGE_TYPE AS change_type, IFNULL(_CHANGE_IS_FOR_UPDATE, FALSE) AS is_for_update
FROM CHANGES(TABLE `{ds}.shelf_prices`, NULL, NULL)
ORDER BY commit_no, sku, change_type
