-- name: databricks
-- Which households can each store deliver to? A spatial join whose ON condition is the ST_DWithin predicate, now
-- supported as a join condition for spatial joins (Databricks SQL 2026.15), with points built by the new
-- st_makepoint (same release). Coordinates are metres on a flat local grid (SRID 0), so ST_DWithin's planar
-- distance is in metres too. {stores} / {homes} are inline VALUES lists built from data/*.csv, and {radius} is metres.
WITH stores AS (
  SELECT store_id, store, st_makepoint(x_m, y_m) AS geom FROM VALUES
{stores}
  AS t(store_id, store, x_m, y_m)
), homes AS (
  SELECT home_id, st_makepoint(x_m, y_m) AS geom FROM VALUES
{homes}
  AS t(home_id, x_m, y_m)
)
SELECT h.home_id, s.store_id, CAST(round(st_distance(h.geom, s.geom)) AS INT) AS dist_m
FROM homes h
INNER JOIN stores s
  ON st_dwithin(h.geom, s.geom, {radius})
ORDER BY h.home_id, dist_m;

-- name: local
-- The same pairs without geometry types (DuckDB here): a cross join filtered on Euclidean distance.
WITH stores AS (
  SELECT * FROM read_csv('{stores_csv}', header = true)
), homes AS (
  SELECT * FROM read_csv('{homes_csv}', header = true)
)
SELECT h.home_id, s.store_id,
       CAST(round(sqrt((h.x_m - s.x_m) ^ 2 + (h.y_m - s.y_m) ^ 2)) AS INT) AS dist_m
FROM homes h CROSS JOIN stores s
WHERE sqrt((h.x_m - s.x_m) ^ 2 + (h.y_m - s.y_m) ^ 2) <= {radius}
ORDER BY h.home_id, dist_m
