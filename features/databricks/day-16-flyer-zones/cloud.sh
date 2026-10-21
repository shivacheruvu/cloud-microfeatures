#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse (Free Edition compatible), build each flyer
# zone with st_makeenvelope (Databricks SQL 2026.15) from two corners typed in any order, join households with
# st_contains, and check zones, shapes, areas and (zone, home) pairs against the local DuckDB box filter.
# Read-only: nothing is created.
set -euo pipefail
python zones.py --databricks
