#!/usr/bin/env bash
# Cloud run (GitHub Actions): create a scratch table and a metric view with numeric-index window measures on a
# Databricks serverless SQL warehouse, query it, check it equals the local result, then drop the view, table and schema.
set -euo pipefail
pip install -q duckdb
python fiscal_weeks.py --databricks
