#!/usr/bin/env bash
# Cloud run (GitHub Actions): on Databricks Free Edition, run report.sql on the serverless SQL warehouse against the
# Lakeflow pipelines system tables (system.lakeflow.pipelines + pipeline_update_timeline, GA 2026-10-21) for the last
# 7 days, check the report's counts add up and include day 18's pipeline, and check the old schema name
# (system.workflow) gives the same report. Read-only: nothing is created, so nothing needs deleting.
set -euo pipefail
python run_report.py --databricks
