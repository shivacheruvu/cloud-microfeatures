-- Day 22 (Google Cloud): which store exports landed in the drop bucket? OBJ.LIST (GA 2026-09-30) lists the files
-- and their metadata straight from Cloud Storage: no object table, no connection (direct access, same project).
WITH listed AS (
  SELECT REGEXP_EXTRACT(uri, r'/exports/([a-z]+)_\d{4}-\d{2}-\d{2}\.csv$')            AS store,
         SAFE.PARSE_DATE('%F', REGEXP_EXTRACT(uri, r'_(\d{4}-\d{2}-\d{2})\.csv$'))   AS export_date,
         size
  FROM OBJ.LIST('gs://{bucket}/exports/*')
),
expected AS (
  SELECT store, export_date
  FROM UNNEST(['east', 'north', 'south']) AS store,
       UNNEST(GENERATE_DATE_ARRAY(DATE '2026-10-23', DATE '2026-10-25')) AS export_date
)
SELECT e.store,
       FORMAT_DATE('%F', e.export_date) AS export_date,
       CASE WHEN l.size IS NULL THEN 'missing' WHEN l.size = 0 THEN 'empty' ELSE 'ok' END AS status,
       IFNULL(l.size, 0) AS bytes
FROM expected AS e
LEFT JOIN listed AS l USING (store, export_date)
ORDER BY store, export_date
