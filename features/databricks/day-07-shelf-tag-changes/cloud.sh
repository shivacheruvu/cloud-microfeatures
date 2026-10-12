#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse, create a row-tracking table (no change data
# feed property), run the four price steps, read table_changes(), check it against the local snapshot diff, then
# drop the tables and schema.
set -euo pipefail
pip install -q duckdb
python shelf_changes.py --databricks
