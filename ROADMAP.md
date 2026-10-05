# Roadmap

One microfeature per day for 80 days, starting 2026-10-05.
Each one is small, runs end to end, and shows one data-engineering skill a hiring manager would recognise.
The running theme is **grocery data**: [ANTeater](https://github.com/shivacheruvu/ANTeater) scan exports
and the public [Open Food Facts](https://world.openfoodfacts.org/data) dataset (ODbL), so no private data is ever used.

## Phase 1 — Databricks (days 1–14, 14-day free trial)

| Day | Microfeature | Shows |
|---|---|---|
| 1 | Medallion pipeline for ANTeater scan exports (bronze → silver → gold) | Lakeflow Declarative Pipelines, Delta Lake |
| 2 | Unity Catalog as code: catalog, schemas, volumes, grants | Unity Catalog, governance |
| 3 | Incremental ingest of Open Food Facts daily delta files | Auto Loader, schema evolution |
| 4 | Data quality expectations with a quarantine table | Expectations, DQ metrics |
| 5 | Product history with SCD Type 2 | AUTO CDC, slowly changing dimensions |
| 6 | Multi-task job with retries, alerts and a schedule, deployed from git | Lakeflow Jobs, Declarative Automation Bundles |
| 7 | Trial cost guardrails: compute policy, auto-termination, spend report | Compute policies, system billing tables |
| 8 | Governed grocery KPIs | Unity Catalog metric views |
| 9 | Classify ingredients and additives with SQL AI functions | ai_classify, ai_extract |
| 10 | Grocery health dashboard | AI/BI dashboards |
| 11 | Table layout benchmark before and after | Liquid clustering, OPTIMIZE, VACUUM |
| 12 | Schema drift and primary-key reconciliation utility | PySpark, data contracts |
| 13 | Streaming scan events with exactly-once output | Structured Streaming, checkpoints |
| 14 | Wrap-up: export every result, move a lite version to Free Edition, confirm nothing bills | Cost control, portability |

## Phase 2 — Google Cloud (days 15–80, free-trial credits)

Built in roughly this order; the daily session picks the next unbuilt item and adds new ideas when fewer than 10 remain.

1. Budget alert + spend kill switch (Budgets, Pub/Sub, Cloud Run function)
2. Keyless GitHub Actions → GCP (Workload Identity Federation)
3. Raw data lake bucket with lifecycle rules (Cloud Storage classes)
4. BigQuery dataset with partitioning, clustering and a query-cost cap (maximum bytes billed)
5. Load Open Food Facts exports to BigQuery (load jobs, schemas)
6. Scheduled daily ELT (BigQuery scheduled queries)
7. Serverless ingestion job on a timer (Cloud Run jobs + Cloud Scheduler)
8. Event-driven ingest on file upload (Eventarc)
9. Data quality checks with assertions (Dataform)
10. dbt-style transformations in Dataform (SQLX, dependencies)
11. Change data capture into BigQuery (Datastream or MERGE patterns)
12. Pub/Sub → BigQuery streaming subscription
13. Batch pipeline on Dataflow / Apache Beam (Flex Template)
14. Serverless Spark batch (Dataproc Serverless)
15. Orchestrate a DAG (Cloud Composer alternative: Workflows)
16. Secrets done right (Secret Manager + least-privilege service accounts)
17. Row- and column-level security (BigQuery policy tags)
18. Data catalog and lineage (Dataplex)
19. BigQuery ML forecast of grocery prices (ARIMA_PLUS)
20. Anomaly detection in SQL (BigQuery AI functions)
21. Gemini in BigQuery: classify ingredients with SQL (AI.GENERATE)
22. Vector search over product descriptions (BigQuery vector index)
23. Looker Studio dashboard over curated tables
24. Cost report from billing export
25. Infrastructure as code for the whole stack (Terraform)
26. CI for SQL: dry-run every query in pull requests
27. Observability: logs-based metrics and an alert (Cloud Monitoring)
28. API on top of the gold layer (Cloud Run + BigQuery)
29. Data contract tests between layers
30. Backfill tool with idempotent reruns

## Rules for every day

See [CLAUDE.md](CLAUDE.md). In short: small and finished beats big and half-done, no credentials in the repo,
smallest compute that works, and every feature has a local test that passes in CI without cloud access.
