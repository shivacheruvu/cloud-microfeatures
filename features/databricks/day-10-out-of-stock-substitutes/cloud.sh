#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse, find the two nearest in-stock substitutes for
# each out-of-stock item with the NEAREST BY join and check them against the local DuckDB cross-join ranking.
# Read-only: nothing is created.
set -euo pipefail
python substitutes.py --databricks
