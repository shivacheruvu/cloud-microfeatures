# Day 19 · Databricks · Pipeline run report from the Lakeflow system tables

**New platform feature:** **Lakeflow pipelines system tables**, **generally available 2026-10-21**:
[release note](https://docs.databricks.com/aws/en/release-notes/product/2026/october) ("Lakeflow pipelines system
tables" GA). Reference: [Jobs and pipelines system tables](https://docs.databricks.com/aws/en/admin/system-tables/jobs).
`system.lakeflow.pipelines` is an SCD2 table (a row per change; a deletion is a row with `delete_time`) and
`system.lakeflow.pipeline_update_timeline` has one row per update, or per hour slice of a long update, with
`result_state` (`COMPLETED`, `FAILED`, `CANCELED`) on the last row only. The schema used to be called
`system.workflow`; the docs say the content of both is identical. Records arrive within about an hour, kept 365 days.

## What it does
`report.sql` answers "how did this project's pipelines run this week?" with one row per pipeline name
(`name LIKE 'microfeatures-%'`), for updates that started at or after `:since` (7 days ago):

| Column | Meaning |
|---|---|
| `pipeline_ids`, `deleted_ids` | each daily run of day 18 creates a fresh pipeline and deletes it, so one name has many IDs |
| `updates`, `completed`, `failed`, `canceled`, `running` | outcome of each update (an update split over hours counts once) |
| `full_refreshes`, `avg_seconds`, `max_seconds` | update type and run time summed over its hour slices |
| `last_result` | outcome of the most recent update (`RUNNING` if it hasn't ended) |

The SQL handles the three traps of these tables: the latest *named* SCD2 row (a delete row may carry no name, and a
pipeline can be renamed), deletion taken from any row, and an update over an hour boundary summed into one.

## Why it matters
Day 18's pipeline exists for about five minutes per run; afterwards the Pipelines API can't see it. The system tables
keep every short-lived pipeline and update for a year, so "did the store pipeline fail this week and how long does it
take?" is one SQL statement on the warehouse instead of scraping event logs before the pipeline is deleted.

## How to run
```bash
python features/databricks/day-19-pipeline-run-report/run_report.py --local       # DuckDB over the fixture; writes result.json
python features/databricks/day-19-pipeline-run-report/run_report.py --databricks  # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day19_databricks_pipeline_system_tables.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions: `report.sql` goes to the Free Edition serverless SQL warehouse
through the Statement Execution API with `:since` and `:name_like` as typed parameters. It fails if the system tables
can't be read, if day 18's pipeline (which runs twice in every daily run) has no updates in the window, or if a row's
counts don't add up; it also runs the same report against `system.workflow` and fails if that differs (a warning if
the old name isn't available). Real rows can't equal a synthetic copy, so the local run checks the SQL instead: DuckDB
runs `report.sql` (with `timestampdiff` and the catalog name translated) over a synthetic copy of the two tables and
must equal a plain-Python model of the same rules.

## Cost
Two read-only queries on the Free Edition serverless warehouse: no charge. Nothing is created.

## Result (local fixture: 2026-10-16 15:00 to 10-23 15:00 UTC)
| Pipeline | IDs (deleted) | Updates | Completed | Failed | Running | Avg s | Max s | Last |
|---|---|---|---|---|---|---|---|---|
| microfeatures-day18-store-params | 4 (4) | 8 | 8 | 0 | 0 | 138 | 205 | COMPLETED |
| microfeatures-dev-store-params | 1 (0) | 4 | 2 | 1 | 1 | 1069 | 3900 | RUNNING |

The fixture also holds an older day-18 run (before the window), a pipeline that was renamed into the filter, an update
split over 11:00 and someone else's pipeline (filtered out). The cloud run's numbers are in the run's annotations and
the daily report.
