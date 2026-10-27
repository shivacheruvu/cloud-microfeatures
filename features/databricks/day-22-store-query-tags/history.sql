-- name: tag_rollup (reads the tags back from the account-wide query history)
SELECT query_tags['store']  AS store,
       query_tags['report'] AS report,
       COUNT(*)             AS statements,
       SUM(produced_rows)   AS produced_rows
FROM system.query.history
WHERE statement_id IN ({statement_ids})
  AND MAP_CONTAINS_KEY(query_tags, 'feature')
  AND query_tags['feature'] = 'day22'
GROUP BY ALL
ORDER BY store
