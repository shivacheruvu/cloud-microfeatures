# Cloud & Data Infra Microfeatures

Two small, working data-engineering features a day for 80 days: one on **Databricks** and one on
**Google Cloud**. Each one is built on a platform feature released in the last six months (checked against the
official release notes that morning), runs end to end, and has a test.

Live progress: [shivacheruvu.github.io/projects.html](https://shivacheruvu.github.io/projects.html#section-microtools) ·
Plan: [ROADMAP.md](ROADMAP.md)

[![Tests](https://github.com/shivacheruvu/cloud-microfeatures/actions/workflows/ci.yml/badge.svg)](https://github.com/shivacheruvu/cloud-microfeatures/actions/workflows/ci.yml)
[![Secret scan](https://github.com/shivacheruvu/cloud-microfeatures/actions/workflows/secret-scan.yml/badge.svg)](https://github.com/shivacheruvu/cloud-microfeatures/actions/workflows/secret-scan.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Databricks features

Days 1–14 run on the Databricks 14-day trial, then on Databricks Free Edition.

<!-- databricks:start -->
| Day | Microfeature | New platform feature | Shows | Status |
|---|---|---|---|---|
| 1 | [Scan rush hours with time_bucket](features/databricks/day-01-scan-rush-hours) | [time_bucket SQL function](https://docs.databricks.com/aws/en/release-notes/product/2026/september) (2026-09-08) | Databricks SQL, Serverless SQL warehouse, Statement Execution API | ran |
| 2 | [Lane counters with counter_diff](features/databricks/day-02-lane-counters) | [counter_diff SQL window function](https://docs.databricks.com/aws/en/release-notes/product/2026/september) (2026-09-14) | Databricks SQL, Window functions, Statement Execution API | ran |
| 3 | [Barcode audit with a Unity Catalog Python UDF](features/databricks/day-03-barcode-udf) | [Scalar Unity Catalog Python UDFs with named handlers (GA)](https://docs.databricks.com/aws/en/release-notes/product/2026/september) (2026-09-23) | Unity Catalog, Python UDFs, Statement Execution API | ran |
| 4 | [Rolling grocery sales with metric view window measures](features/databricks/day-04-rolling-sales-metric-view) | [Metric view window measures (GA)](https://docs.databricks.com/aws/en/release-notes/product/2026/october) (2026-10-06) | Unity Catalog metric views, Databricks SQL, Statement Execution API | built |
<!-- databricks:end -->

## Google Cloud features

<!-- gcp:start -->
| Day | Microfeature | New platform feature | Shows | Status |
|---|---|---|---|---|
| 1 | [One-pass category scorecard with WHERE inside aggregates](features/gcp/day-01-filtered-aggregates) | [WHERE clause in aggregate functions (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-09-14) | BigQuery, GoogleSQL, Workload Identity Federation | ran |
| 2 | [Grading grocery predictors with ML.METRICS](features/gcp/day-02-ml-metrics) | [ML.METRICS function (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-09-10) | BigQuery, BigQuery ML, Workload Identity Federation | ran |
| 3 | [What moved this week's grocery sales? AI.KEY_DRIVERS](features/gcp/day-03-key-drivers) | [AI.KEY_DRIVERS function (GA)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-09-29) | BigQuery, BigQuery AI functions, Workload Identity Federation | ran |
<!-- gcp:end -->

## Run any of them yourself

Every feature folder has its own README. Each one runs locally with no cloud account (`pytest`), and on your
own Databricks or Google Cloud account using your own credentials:

```bash
cp .env.example .env     # fill in YOUR workspace / project; .env is git-ignored
pip install -r requirements-dev.txt
pytest
```

Nothing in this repo can spend anyone else's money: there are no keys in the code, cloud access in CI uses
GitHub Actions secrets and keyless Workload Identity Federation, and every push is secret-scanned.
See [SECURITY.md](SECURITY.md).

## How it's built

Each day's feature is built by Claude following [CLAUDE.md](CLAUDE.md), merged through a pull request that must
pass the secret scan and tests, and summarised in [`reports/`](reports/).
