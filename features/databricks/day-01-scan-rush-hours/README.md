# Day 1 · Databricks · Scan rush hours with `time_bucket`

**New platform feature:** [`time_bucket` SQL function](https://docs.databricks.com/aws/en/sql/language-manual/functions/time_bucket),
released **2026-09-08** ([release notes](https://docs.databricks.com/aws/en/release-notes/product/2026/september)).

## What it does
Groups a store day of grocery scans (ANTeater-style events: time, barcode, category, health score) into
**30-minute slots aligned to the store's 07:15 opening**, then reports scans, average health score and the share of
healthy products (score ≥ 70) per slot.

## Why it matters
Operational data rarely lines up with clock hours: shifts start at :15, delivery windows run 45 minutes, fiscal
quarters start mid-month. `date_trunc` can only cut on calendar units. `time_bucket(width, ts, origin)` cuts on
any fixed width from any origin, in one function call that's easy to read and to index on.

```sql
time_bucket(INTERVAL '30' MINUTE, scanned_at, TIMESTAMP '1970-01-01 00:15:00') AS slot_start
```

## How to run
```bash
python rush_hours.py --local        # DuckDB runs the same SQL; writes result.json
python rush_hours.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN; runs on a serverless SQL warehouse
```
In CI, `cloud.sh` runs the query on Databricks through the SQL Statement Execution API and fails unless the
result is identical to the local one.

## Cost
One small statement on a serverless SQL warehouse (seconds of compute, plus the warehouse's idle time until it
auto-stops). Covered by the 14-day trial credits.

## Result (43 sample scans)
| Metric | Value |
|---|---|
| Slots with scans | 13 |
| Busiest slot | 12:15–12:45 (6 scans) |
| Healthiest slot | 09:15 (avg score 97.0) |

Full per-slot table: [`result.json`](result.json). The sample scans are synthetic (seeded), one store day.
