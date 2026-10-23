# Day 18 · Databricks · One store-sales pipeline, any store, with pipeline parameters

**New platform feature:** **pipeline parameters** for Lakeflow pipelines, **generally available 2026-10-12**:
[release note](https://docs.databricks.com/aws/en/release-notes/product/2026/october) ("Pipeline parameters" GA:
parameterize pipeline SQL with named parameter syntax and override values per run). Reference:
[Use parameters with pipelines](https://docs.databricks.com/aws/en/ldp/parameters). Parameters are key/value strings
saved on the pipeline as defaults; SQL source reads them as named parameter markers (`:store_id`); a single update
can override them (`parameters` on the start-update request, a job's pipeline task, or "Run with different settings")
without changing the saved defaults. Serverless pipelines are part of **Databricks Free Edition**.

## What it does
One SQL file (`pipeline.sql`) defines two materialized views: `scans` (94 synthetic ANTeater-style scan lines for
three stores over the week of 12–18 Oct) and `store_daily`:

```sql
CREATE OR REFRESH MATERIALIZED VIEW store_daily AS
SELECT store_id, scan_date, count(*) AS scan_lines, sum(qty) AS units, sum(qty * unit_cents) AS sales_cents
FROM scans
WHERE store_id = :store_id
  AND scan_date >= CAST(:start_date AS DATE)
GROUP BY store_id, scan_date;
```

The pipeline is created with saved parameters `{"store_id": "S1", "start_date": "2026-10-12"}` and run twice:

| Run | How the values arrive | Store | From | Days | Units | Sales |
|---|---|---|---|---|---|---|
| defaults | pipeline settings | S1 | 10-12 | 7 | 142 | $569.08 |
| override | `parameters` on this update only | S2 | 10-15 | 4 | 86 | $311.34 |

## Why it matters
Before parameters, a "same pipeline for another store" meant a copy of the pipeline, string-built SQL, or Spark
`${conf}` substitution with no record of what a run used. Now the store manager's view is one pipeline: the scheduled
run uses the saved store, an ad-hoc run (or a job's pipeline task) passes another store and start date, and each
update records the values it ran with. A marker with no value fails the update instead of silently reading
everything.

## How to run
```bash
python features/databricks/day-18-store-pipeline-parameters/store_pipeline.py --local       # DuckDB; writes result.json
python features/databricks/day-18-store-pipeline-parameters/store_pipeline.py --render      # regenerate pipeline.sql
python features/databricks/day-18-store-pipeline-parameters/store_pipeline.py --databricks  # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day18_databricks_pipeline_parameters.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions: it uploads `pipeline.sql` to a `/Shared` workspace folder,
creates a serverless development-mode pipeline in a scratch schema with the saved defaults, runs a full-refresh
update with the defaults and another with the override, reads `store_daily` through the Statement Execution API
after each, and deletes the pipeline, the folder and the schema. It fails if an update doesn't complete, if either
table differs from the local DuckDB run of the same SQL with the same values, or if the override changed the saved
defaults. Locally, DuckDB runs `pipeline.sql` with the markers bound the way the pipeline binds them (an unbound
marker raises), and a plain-Python model checks both runs.

## Cost
Two short serverless pipeline updates (two tiny materialized views each) and a few SQL statements on the Free Edition
serverless warehouse: no charge. Everything is deleted in the same run.

## Result
| Run | Date | Scan lines | Units | Sales (cents) |
|---|---|---|---|---|
| defaults (S1) | 10-12 | 4 | 14 | 6326 |
| | 10-13 | 4 | 17 | 6643 |
| | 10-14 | 5 | 30 | 11240 |
| | 10-15 | 4 | 20 | 8850 |
| | 10-16 | 4 | 20 | 7400 |
| | 10-17 | 4 | 23 | 8347 |
| | 10-18 | 4 | 18 | 8102 |
| override (S2) | 10-15 | 4 | 19 | 5671 |
| | 10-16 | 5 | 19 | 8181 |
| | 10-17 | 5 | 22 | 8498 |
| | 10-18 | 4 | 26 | 8784 |

One pipeline, two stores: **S1** from 10-12 (saved defaults) **$569.08** over 7 days; **S2** from 10-15 (per-run
override) **$311.34** over 4 days; the saved defaults are unchanged after the override.
