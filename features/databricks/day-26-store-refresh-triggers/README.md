# Day 26 · Databricks · One store price-refresh job, several triggers: multiple triggers for Lakeflow Jobs

**New platform feature:** **multiple triggers for Lakeflow Jobs** (Beta): a job's settings take a `triggers` array of
up to five triggers (schedule, file arrival, periodic, table update, ...), mixed freely except with a continuous
trigger, each with its own `pause_status`; any live trigger that fires starts a run. Announced in the
[Databricks platform release notes, October 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/october)
on **2026-10-05** ([docs: Automate jobs with schedules and triggers](https://docs.databricks.com/aws/en/jobs/triggers.html)).

## What it does
The store price-refresh job has needed different "when to run" rules over time
([`data/triggers.json`](data/triggers.json)):

| Version | Why | Triggers | Live | Scheduled runs a week |
|---|---|---|---|---|
| v1 | prices land overnight from head office | nightly 03:30 Chicago | 1 | 7 |
| v2 | stores drop their own price files | nightly + file arrival on `price_drop/east/` + `price_drop/west/` | 3 | 7 (+ one per store drop) |
| v3 | holiday price freeze | nightly **paused alone**, both store folders live, 6-hourly periodic safety refresh | 3 of 4 | 28 |

Before this Beta a job had one trigger, so v2 and v3 needed several near-copies of the same job. `triggers.py` keeps
it to one job: a reconciler reads the job, compares the normalised triggers (defaults filled in, order ignored) with
the wanted version and makes one call: create, update with the **complete** array (an update replaces the whole
list), or nothing. It also checks the documented rules (at most five, not with the legacy `schedule` / `trigger` /
`continuous` fields, a volume path for file arrival, minimum wait times).

## Why it matters
Pausing just the nightly run for a holiday freeze, while store price drops keep flowing, is now one field on one
trigger instead of a second job to keep in sync.

## How to run
```bash
python features/databricks/day-26-store-refresh-triggers/triggers.py        # local, writes result.json
pytest -q tests/test_day26_databricks_multiple_triggers.py
```
The local run uses an in-memory Jobs API that follows the documented rules; the test also runs the real REST client
over HTTP against a local server. `cloud.sh` (GitHub Actions) runs the same steps on the workspace through the Jobs
API 2.2 (create / get / update / delete only: **no compute, nothing is ever run**), in a scratch schema
`mf26_prices` with two managed volumes, and checks every step's outcome and the triggers read back equal the local
run. The job, the volumes and the schema are deleted on exit. If the workspace refuses a `triggers` array (the Beta
is switched on per workspace, Previews page, "Multiple Triggers"), the run warns and nothing is left behind.

## Cost
REST calls only; the job is deleted seconds after it is created, before any trigger can fire. $0.

## Result
Local: 7 steps, 6 calls that change the job (create, two full-array updates, two refused updates, delete) and 1
no-op re-apply. The two updates the API must refuse (six triggers; `triggers` together with the legacy `schedule`)
left the v3 triggers untouched. v3: 3 of 4 triggers live, only the nightly schedule paused, east and west folders
still watched, 28 scheduled runs a week. Cloud result: see the day's report. Full numbers in
[`result.json`](result.json).
