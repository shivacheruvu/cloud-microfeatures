-- name: databricks
-- Which households does each flyer drop reach? Each zone is a rectangle the flyer team typed as two corners, in any
-- order: st_makeenvelope (Databricks SQL 2026.15) normalises the corners and returns the box as a GEOMETRY (SRID 0,
-- so coordinates are metres on a flat local grid). st_npoints tells a real box (5-vertex ring) from a mistyped zone
-- that collapsed to a line (2 points) or a point (1). {zones} / {homes} are inline VALUES lists built from data/*.csv.
WITH zones AS (
  SELECT zone_id, zone, st_makeenvelope(x1, y1, x2, y2) AS box FROM VALUES
{zones}
  AS t(zone_id, zone, x1, y1, x2, y2)
), homes AS (
  SELECT home_id, st_makepoint(x_m, y_m) AS geom FROM VALUES
{homes}
  AS t(home_id, x_m, y_m)
)
SELECT z.zone_id, st_npoints(z.box) AS npoints, CAST(round(st_area(z.box)) AS BIGINT) AS area_m2,
       h.home_id
FROM zones z
LEFT JOIN homes h
  ON st_contains(z.box, h.geom)
ORDER BY z.zone_id, h.home_id;

-- name: local
-- The same rows without geometry types (DuckDB here): normalise the corners with least/greatest, count a home as
-- inside when it is strictly inside the box (a line or point has no interior, so it contains nothing).
WITH zones AS (
  SELECT zone_id, least(x1, x2) AS xmin, greatest(x1, x2) AS xmax, least(y1, y2) AS ymin, greatest(y1, y2) AS ymax
  FROM read_csv('{zones_csv}', header = true)
), homes AS (
  SELECT * FROM read_csv('{homes_csv}', header = true)
)
SELECT z.zone_id,
       CASE WHEN xmin = xmax AND ymin = ymax THEN 1 WHEN xmin = xmax OR ymin = ymax THEN 2 ELSE 5 END AS npoints,
       CAST((xmax - xmin) * (ymax - ymin) AS BIGINT) AS area_m2,
       h.home_id
FROM zones z
LEFT JOIN homes h
  ON h.x_m > z.xmin AND h.x_m < z.xmax AND h.y_m > z.ymin AND h.y_m < z.ymax
ORDER BY z.zone_id, h.home_id
