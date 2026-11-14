#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits store_day_mv.py as a one-off serverless job
# (environment version 6) that creates a till-receipts table and a standalone materialized view of units and revenue
# per store per day with CREATE MATERIALIZED VIEW through spark.sql (standalone pipelines on serverless general compute,
# GA 2026-11-11), adds late receipts, checks the view still gives the old answer, runs REFRESH MATERIALIZED VIEW and
# reads it again. All three reads must equal the local reference (result.json). The workspace folder and the schema
# are deleted on exit. The hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
