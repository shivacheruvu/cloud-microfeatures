#!/usr/bin/env bash
# Cloud run (GitHub Actions): on Databricks Free Edition, upload pipeline.sql to a workspace folder, create a
# serverless Lakeflow pipeline whose saved parameters are store S1 from 2026-10-12, run one update with those
# defaults and one with a per-run override (store S2 from 2026-10-15; pipeline parameters, GA 2026-10-12), read
# store_daily after each, and check both against local DuckDB runs of the same SQL and that the override left the
# saved defaults alone. The pipeline, the workspace folder and the scratch schema are deleted on exit.
set -euo pipefail
python store_pipeline.py --databricks
