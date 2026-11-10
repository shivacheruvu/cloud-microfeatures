# Day 36 · Databricks · Markdown planning with dates from the job itself: date arithmetic in job parameters

**New platform feature:** **date arithmetic in Lakeflow Jobs dynamic value references** (Beta): a time reference can
be shifted by a duration before it is formatted, `{{(<time_reference> <+ or -> <amount>.<unit>).<argument>}}`, e.g.
`{{(job.start_time - 1.day).iso_date}}`; units are minutes to years, arguments the usual date/time formats.
Announced in the
[Databricks platform release notes, November 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/november)
on **2026-11-09** ([docs: Dynamic value references, "Date arithmetic"](https://docs.databricks.com/aws/en/jobs/dynamic-value-references)).
A workspace admin switches it on (Previews page, *Math in Parameters – Expression support in job parameters*).

## What it does
The daily markdown-planning job for two stores takes **every date as a job parameter** instead of doing
`current_date()` maths in SQL ([`date_refs.py`](date_refs.py) `PARAMS`):

| Parameter | Reference | Run starting 2026-11-09T11:00:07.123Z |
|---|---|---|
| run_date | `{{job.start_time.iso_date}}` | 2026-11-09 |
| yesterday | `{{(job.start_time - 1.day).iso_date}}` | 2026-11-08 |
| yesterday_last_week | `{{(job.start_time - 8.days).iso_date}}` | 2026-11-01 |
| week_start | `{{(job.start_time - 1.week).iso_date}}` | 2026-11-02 |
| sell_by_cutoff | `{{(job.start_time + 3.days).iso_date}}` | 2026-11-12 |
| last_month / last_month_year | `{{(job.start_time - 1.month).month}}` / `.year` | 10 / 2026 |
| same_day_last_year | `{{(job.start_time - 1.year).iso_date}}` | 2025-11-09 |
| six_hours_ago_ms | `{{(job.start_time - 6.hours).timestamp_ms}}` | 1794200407123 |
| ninety_min_ago_hour | `{{(job.start_time - 90.minutes).hour}}` | 9 |
| tomorrow_iso_weekday | `{{(job.start_time + 1.day).iso_weekday}}` | 2 |

[`markdown_plan.sql`](markdown_plan.sql) uses them: 7-day sales velocity per store and SKU (`week_start` to
`run_date`), every lot with a sell-by on or before `sell_by_cutoff`, and an action per lot: **pull** (past sell-by),
**keep** (expected sales cover it), **50% off** (0-1 days left) or **25% off** (2-3 days).

`date_refs.py` is a stdlib resolver for the documented syntax (both references, every unit and argument, the 2,000-year
and 0001-9999 limits), checked against the four documented examples and six edge cases. Month and year shifts on a
day the target month doesn't have aren't documented; the resolver assumes clamping to month end
(2026-03-31 − 1 month = 2026-02-28), as java.time does.

## Cloud run
`cloud.sh` → [`run_cloud.py`](run_cloud.py): creates a one-task job whose `spark_python_task` parameters are the
references above, runs it once on serverless (environment version 6), and [`task_echo.py`](task_echo.py) writes what
it received. Every value must equal `date_refs.resolve()` for the run's own `{{job.start_time.timestamp_ms}}`. The
job, folder, volume and schema are deleted on exit. Unresolved references (Beta off) or a refused account → warning.

## Why it matters
Backfills and re-runs: a run's dates are fixed when the run starts and visible in the run's parameters, so the SQL
needs no clock and the same query runs locally with any date. Before this Beta, "yesterday" or "last week" meant date
maths in every task.

## How to run
```bash
python features/databricks/day-36-markdown-window-dates/markdown_window.py   # local DuckDB run, writes result.json
pytest -q tests/test_day36_databricks_date_arithmetic.py
```

## Cost
One serverless Python task of a few seconds when the workspace is active (Free Edition: no charge); otherwise REST
calls only. $0.

## Result (local)
13 lots due by 2026-11-12: **2 pull** (51 units), **5 at 50% off**, **3 at 25% off** (298 units to mark down),
**3 keep**. Yesterday 153 units across both stores vs 160 the same weekday last week. Resolver: 10/10 edge cases,
including the 4 documented examples. Cloud: see the day's report. Numbers in [`result.json`](result.json).
