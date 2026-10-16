#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse, find every household within 2.5 km of each
# grocery store with a spatial join ON st_dwithin(...) over st_makepoint points, and check the pairs and distances
# against the local DuckDB distance filter. Read-only: nothing is created.
set -euo pipefail
pip install -q duckdb   # the local cross-join filter it's compared with
python coverage.py --databricks
