#!/usr/bin/env bash
# Cloud run (GitHub Actions): same query on a Databricks serverless SQL warehouse, checked against the local result.
set -euo pipefail
pip install -q duckdb
python rush_hours.py --databricks
