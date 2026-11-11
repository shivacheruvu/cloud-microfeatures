# Day 37 · Databricks · Restock plan that starts only after both receiving jobs finish: job completion triggers

**New platform feature:** **Job completion triggers for Lakeflow Jobs** (Beta): a job can be triggered when 1-10
upstream jobs complete (also in other workspaces on the same metastore), filtered by `if_status` (`SUCCESS`,
`FAILED`, `DONE`), and gets the contributing runs through `{{job.trigger.job_completion.completed_jobs}}`. Announced
in the [Databricks platform release notes](https://docs.databricks.com/aws/en/release-notes/product/2026/november) on
**2026-11-03** ([Job completion triggers](https://docs.databricks.com/aws/en/jobs/job-completion-triggers)).

## What it does
A store's morning has two receiving jobs: `receive-dc-truck` (the distribution-center truck) and
`receive-dsd-vendors` (direct-store-delivery vendors). The **restock plan** job should run once both have landed, not
on a clock. Its settings ([`completion_trigger.py`](completion_trigger.py) `job_settings`):

```json
"trigger": {"pause_status": "UNPAUSED",
            "job_completion": {"jobs": [{"workspace_id": "…", "job_id": "<dc>"}, {"workspace_id": "…", "job_id": "<dsd>"}],
                               "if_status": "SUCCESS"}},
"parameters": [{"name": "upstream_completions", "default": "{{job.trigger.job_completion.completed_jobs}}"}]
```

`Trigger` is a small stdlib model of the documented rules (AND across jobs, latest matching completion per job wins,
`SUCCESS` also matches `SUCCESS_WITH_FAILURES`, nothing from before the trigger existed, every job must complete
again before the next run). A replayed receiving day of 7 upstream runs (a pre-trigger run, an ASN correction re-run,
a failed vendor file, an afternoon vendor drop) shows which runs each restock plan consumes, and the task turns the
cases received into shelf / backroom moves.

**Cloud** ([`run_cloud.py`](run_cloud.py)): creates the three serverless jobs, runs each receiving job once, waits for
the trigger to start the restock job and checks `trigger_info.job_completion.completed_jobs` (and the task's
`upstream_completions` parameter) lists exactly those two runs in configuration order. Everything is deleted on exit.

## Why it matters
Fixed schedules either start before late deliveries land or wait too long. A completion trigger starts the plan as
soon as both inputs are in, and tells the task *which* runs it is built on, so a re-run upstream (the corrected ASN)
replaces the stale one instead of double counting it, and a failed vendor load never produces a half plan.

## How to run
```bash
python features/databricks/day-37-restock-after-receiving/completion_trigger.py   # local replay, writes result.json
pytest -q tests/test_day37_databricks_job_completion_trigger.py
DATABRICKS_HOST=... DATABRICKS_TOKEN=... bash features/databricks/day-37-restock-after-receiving/cloud.sh   # GitHub Actions
```
Needs the Beta switched on in the workspace (Previews page: job completion triggers).

## Cost
Three one-task serverless jobs that each run for seconds (Free Edition quota; no SQL warehouse). REST calls only
otherwise. Jobs and the workspace folder are deleted after the check.

## Result (local replay)
| Restock run starts after | DC truck run | DSD vendor run | Cases received → shelf / backroom |
|---|---|---|---|
| 07:30 | 9102 SUCCESS (06:40 re-run replaces 9101) | 9202 SUCCESS (9201 FAILED is skipped) | 94 → 63 / 31 |
| 13:00 | 9103 SUCCESS_WITH_FAILURES | 9203 SUCCESS | 26 → 25 / 1 |

7 upstream completions → **2 restock runs** with `if_status: SUCCESS` (`DONE`: 2, the first one built on the failed
vendor run 9201; `FAILED`: 0). The 05:05 run predates the trigger and never starts anything. 120 cases received,
88 straight to the shelf. Numbers in [`result.json`](result.json).

**Cloud:** the Free Edition account has refused compute since 2026-10-24, so the cloud run is expected to warn and
the trigger rules are verified locally (status `built`).
