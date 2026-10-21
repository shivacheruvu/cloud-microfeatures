# Day 16 · Databricks · Flyer drop zones with `st_makeenvelope`

**New platform feature:** the **`st_makeenvelope`** function ("returns a rectangular polygon from two corner
points"), new in [Databricks SQL 2026.15](https://docs.databricks.com/aws/en/sql/release-notes/2026): preview
channel **2026-05-26**, current channel from 2026-06-03 (Databricks Runtime 18.2+, not on Classic warehouses).
Reference: [st_makeenvelope](https://docs.databricks.com/aws/en/sql/language-manual/functions/st_makeenvelope).
Runs on a serverless SQL warehouse, so it fits **Databricks Free Edition**.

## What it does
A store plans five flyer drops, each zone typed by the marketing team as two corners on the town's 10 km x 10 km
grid (metres, SRID 0). One read-only query builds each zone with `st_makeenvelope(x1, y1, x2, y2)` and joins 80
households to it with `st_contains`:

```sql
SELECT z.zone_id, st_npoints(z.box) AS npoints, round(st_area(z.box)) AS area_m2, h.home_id
FROM zones z                           -- st_makeenvelope(x1, y1, x2, y2) AS box
LEFT JOIN homes h                      -- st_makepoint(x_m, y_m) AS geom
  ON st_contains(z.box, h.geom)
```

The data has the mistakes real zone lists have: **Riverside** was typed with its corners reversed (`st_makeenvelope`
normalises corners, so it still works), **Mill Road** has a typo (`x1 = x2`), which `st_makeenvelope` turns into a
2-point line, and **Southgate** and **Eastfield** overlap. The runner reports each zone's shape (`st_npoints`: 5 =
box, 2 = line, 1 = point), homes reached, homes that get two flyers, and zones to fix before printing.

## Why it matters
Bounding boxes (delivery zones, flyer areas, map viewports, "stores in this region") usually arrive as corner pairs.
Before `st_makeenvelope` you built them from WKT strings or `least`/`greatest` arithmetic; now one function gives a
real `GEOMETRY` that every `st_*` predicate understands, and its documented corner normalisation and degenerate cases
(point, line) make bad input visible instead of silently empty.

## How to run
```bash
python features/databricks/day-16-flyer-zones/zones.py --local        # DuckDB; writes result.json
python features/databricks/day-16-flyer-zones/zones.py --sql          # print the Databricks SQL
python features/databricks/day-16-flyer-zones/zones.py --databricks   # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day16_databricks_st_makeenvelope.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions on a serverless SQL warehouse (Statement Execution API). It
fails if any zone's shape or area, or any (zone, home) pair, differs from the local DuckDB box filter (strictly
inside, corners normalised with `least`/`greatest`). Homes are generated at least 5 m from every zone edge (a test
checks it), so boundary handling can't decide a pair.

## Cost
One small query on a serverless SQL warehouse (Free Edition). Nothing is created.

## Result
| Zone | Typed corners | Shape | Area | Homes | Also in another zone |
|---|---|---|---|---|---|
| North Hill | normal | box | 12.25 km² | 5 | 0 |
| Riverside | **reversed** | box | 11.52 km² | 10 | 0 |
| Southgate | normal | box | 13.5 km² | 9 | 3 |
| Eastfield | normal | box | 12.0 km² | 10 | 3 |
| Mill Road | **x1 = x2 typo** | **line** | 0 | **0** | 0 |

**34 flyers** reach **31 of 80** homes; **3** homes get two (the Southgate/Eastfield overlap), and **Mill Road**
collapsed to a line and would have printed for nobody: fix its corners before the drop.

**Cloud run (2026-10-20, Free Edition):** `st_makeenvelope` + `st_contains` ran on a serverless SQL warehouse in the
post-merge workflow for #62 and matched the local box filter exactly: same shapes (Mill Road a 2-point line), areas
and all 34 (zone, home) pairs.

Full numbers in [`result.json`](result.json).
