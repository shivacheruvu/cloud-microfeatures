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
| 4 | [Rolling grocery sales with metric view window measures](features/databricks/day-04-rolling-sales-metric-view) | [Metric view window measures (GA)](https://docs.databricks.com/aws/en/release-notes/product/2026/october) (2026-10-06) | Unity Catalog metric views, Databricks SQL, Statement Execution API | ran |
| 5 | [Fiscal-week sales on a numeric index metric view](features/databricks/day-05-fiscal-week-sales) | [Window measures on numeric index columns in metric views](https://docs.databricks.com/aws/en/release-notes/product/2026/september) (2026-09-14) | Unity Catalog metric views, Databricks SQL, Statement Execution API | ran |
| 6 | [Re-load a corrected scan export with INSERT ... REPLACE](features/databricks/day-06-idempotent-reload) | [INSERT INTO ... REPLACE USING / REPLACE ON (Databricks SQL 2026.32)](https://docs.databricks.com/aws/en/sql/release-notes/2026) (2026-08-24) | Databricks SQL, Delta Lake, Statement Execution API | ran |
| 7 | [Which shelf tags to reprint? Automatic change data feed](features/databricks/day-07-shelf-tag-changes) | [Automatic change data feed (GA)](https://docs.databricks.com/aws/en/release-notes/product/2026/september) (2026-09-01) | Databricks SQL, Delta Lake, Statement Execution API | ran |
| 8 | [Cut-to-the-cent unit-price labels: truncate and trim_array](features/databricks/day-08-unit-price-labels) | [truncate and trim_array SQL functions (Databricks SQL 2026.38)](https://docs.databricks.com/aws/en/sql/release-notes/2026) (2026-09-21) | Databricks SQL, SQL functions, Statement Execution API | ran |
| 9 | [Which saved report queries still parse, and what they read: parse_sql](features/databricks/day-09-report-query-audit) | [parse_sql SQL function (Databricks SQL 2026.36)](https://docs.databricks.com/aws/en/sql/release-notes/2026) (2026-08-31) | Databricks SQL, SQL functions, Statement Execution API | ran |
| 10 | [Out-of-stock substitutes with the NEAREST BY join](features/databricks/day-10-out-of-stock-substitutes) | [NEAREST BY join (Databricks SQL 2026.20)](https://docs.databricks.com/aws/en/sql/release-notes/2026) (2026-06-29) | Databricks SQL, Joins, Statement Execution API | ran |
| 11 | [Delivery radius with an ST_DWithin spatial join](features/databricks/day-11-delivery-radius) | [ST_DWithin spatial join condition and st_makepoint (Databricks SQL 2026.15)](https://docs.databricks.com/aws/en/sql/release-notes/2026) (2026-05-26) | Databricks SQL, Geospatial, Statement Execution API | ran |
| 12 | [Lane device network audit with the new IP address functions](features/databricks/day-12-lane-device-audit) | [IP address functions: try_ip_host, ip_version, ip_cidr_contains (Databricks SQL 2026.20)](https://docs.databricks.com/aws/en/sql/release-notes/2026) (2026-06-29) | Databricks SQL, IP address functions, Statement Execution API | ran |
| 13 | [Basket and aisle allergens with collect_union](features/databricks/day-13-basket-allergens) | [collect_union aggregate function (Databricks SQL 2026.35)](https://docs.databricks.com/aws/en/sql/release-notes/2026) (2026-08-24) | Databricks SQL, Array aggregates, Statement Execution API | ran |
<!-- databricks:end -->

## Google Cloud features

<!-- gcp:start -->
| Day | Microfeature | New platform feature | Shows | Status |
|---|---|---|---|---|
| 1 | [One-pass category scorecard with WHERE inside aggregates](features/gcp/day-01-filtered-aggregates) | [WHERE clause in aggregate functions (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-09-14) | BigQuery, GoogleSQL, Workload Identity Federation | ran |
| 2 | [Grading grocery predictors with ML.METRICS](features/gcp/day-02-ml-metrics) | [ML.METRICS function (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-09-10) | BigQuery, BigQuery ML, Workload Identity Federation | ran |
| 3 | [What moved this week's grocery sales? AI.KEY_DRIVERS](features/gcp/day-03-key-drivers) | [AI.KEY_DRIVERS function (GA)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-09-29) | BigQuery, BigQuery AI functions, Workload Identity Federation | ran |
| 4 | [When did oat-milk sales shift? ML.DETECT_CHANGE_POINTS](features/gcp/day-04-change-points) | [ML.DETECT_CHANGE_POINTS function (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-08-20) | BigQuery, BigQuery ML time series, Workload Identity Federation | ran |
| 5 | [Which weekday sells the most bread? ML.SEASONALITY](features/gcp/day-05-weekly-seasonality) | [ML.SEASONALITY function (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-08-20) | BigQuery, BigQuery ML time series, Workload Identity Federation | ran |
| 6 | [Is oat milk growing, or just noisy? ML.TREND](features/gcp/day-06-oat-milk-trend) | [ML.TREND function (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-08-20) | BigQuery, BigQuery ML time series, Workload Identity Federation | ran |
| 7 | [Did the shelf reset move sales? AI.CAUSAL_EFFECT](features/gcp/day-07-endcap-effect) | [AI.CAUSAL_EFFECT function (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-09-10) | BigQuery, BigQuery ML, Causal impact | ran |
| 8 | [Product keys BigQuery generates: identity columns](features/gcp/day-08-product-keys) | [Identity columns (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-08-31) | BigQuery, DDL, Data modeling | ran |
| 9 | [How much will a new item sell? Zero-shot AI.PREDICT with TabFM](features/gcp/day-09-new-item-forecast) | [TabFM zero-shot AI.PREDICT (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-08-31) | BigQuery, BigQuery ML, Forecasting | ran |
| 10 | [Promo-aware strawberry forecast: TimesFM 3.0 with a future covariate](features/gcp/day-10-promo-aware-forecast) | [TimesFM 3.0 in AI.FORECAST with covariates (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-10-07) | BigQuery, BigQuery ML, Forecasting | ran |
| 11 | [Scan anomalies with TimesFM 3.0 in AI.DETECT_ANOMALIES](features/gcp/day-11-scan-anomalies) | [TimesFM 3.0 in AI.DETECT_ANOMALIES (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-10-07) | BigQuery, BigQuery ML, Anomaly detection | ran |
| 12 | [Forecast accuracy with TimesFM 3.0 in AI.EVALUATE](features/gcp/day-12-forecast-accuracy) | [TimesFM 3.0 in AI.EVALUATE (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-10-07) | BigQuery, BigQuery ML, Forecast evaluation | ran |
| 13 | [Store-day KPIs with multi-level aggregation](features/gcp/day-13-store-day-kpis) | [Multi-level aggregation (Preview)](https://docs.cloud.google.com/bigquery/docs/release-notes) (2026-07-08) | BigQuery, GoogleSQL, Workload Identity Federation | built |
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
