# Day 11 · Databricks · Delivery radius with an `ST_DWithin` spatial join

**New platform feature:** **`ST_DWithin` as a spatial join condition** ("The `ST_DWithin` (distance within)
predicate is now supported as a join condition for spatial joins"), together with the new **`st_makepoint`**
function, both in [Databricks SQL 2026.15](https://docs.databricks.com/aws/en/sql/release-notes/2026): preview
channel **2026-05-26**, current channel from 2026-06-03. References:
[st_dwithin](https://docs.databricks.com/aws/en/sql/language-manual/functions/st_dwithin),
[st_makepoint](https://docs.databricks.com/aws/en/sql/language-manual/functions/st_makepoint).

## What it does
Four grocery stores and 60 households in a 10 km x 10 km town (coordinates in metres on a flat local grid, so the
geometries have SRID 0 and planar distances are metres). One read-only query over inline `VALUES` returns every
household a store can deliver to within **2.5 km**:

```sql
SELECT h.home_id, s.store_id, CAST(round(st_distance(h.geom, s.geom)) AS INT) AS dist_m
FROM homes h                          -- st_makepoint(x_m, y_m) AS geom
INNER JOIN stores s                   -- st_makepoint(x_m, y_m) AS geom
  ON st_dwithin(h.geom, s.geom, 2500.0)
```

From those pairs the runner works out coverage: homes in reach of at least one store, homes with a choice of stores,
homes outside every radius, and per store how many homes it reaches, is nearest for, and is the only option for.

## Why it matters
"Everything within X metres" (delivery zones, nearby stores, competitor overlap, geofenced promos) is a distance
filter on a join. Written as a plain `WHERE distance <= r` over a cross join, the engine has to compare every pair.
With `ST_DWithin` accepted as a spatial join condition, Databricks can plan it as a real spatial join (and combine it
with other predicates), which is what keeps the same query workable for millions of homes. `st_makepoint` builds
the points straight from numeric columns, no WKT strings.

## How to run
```bash
python features/databricks/day-11-delivery-radius/coverage.py --local        # DuckDB; writes result.json
python features/databricks/day-11-delivery-radius/coverage.py --sql          # print the Databricks SQL
python features/databricks/day-11-delivery-radius/coverage.py --databricks   # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day11_databricks_st_dwithin_join.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions on a serverless SQL warehouse (the Statement Execution API).
It fails if any (home, store) pair is in one result and not the other, or any distance differs by more than 1 m.
The generated data keeps every home at least 5 m away from every radius boundary (a test checks it), so float
rounding can't move a pair in or out.

## Cost
One small query on a serverless SQL warehouse (trial credits; also fine on Free Edition). Nothing is created.

## Result
| Store | Homes within 2.5 km | Nearest store for | Only store for | Median distance |
|---|---|---|---|---|
| Downtown Market | 14 | 13 | 9 | 1581 m |
| Riverside Fresh | 13 | 10 | 7 | 1738 m |
| Southgate Grocer | 13 | 9 | 7 | 1766 m |
| Eastfield Foods | 9 | 7 | 6 | 1932 m |

**39 of 60** homes (65%) are within 2.5 km of a store (49 home-store pairs), **10** can choose between two stores,
and **21** are outside every radius: the gap a fifth store or a wider radius would have to close.

**Cloud run (2026-10-15):** the `ST_DWithin` spatial join ran on a serverless SQL warehouse in the post-merge
workflow and matched the local distance filter exactly (all 49 home-store pairs and their distances).

Full numbers in [`result.json`](result.json).
