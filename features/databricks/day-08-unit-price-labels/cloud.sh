#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse, run the two queries that use truncate() and
# trim_array() and check them against the local exact-fraction result. Read-only: nothing is created.
set -euo pipefail
python labels.py --databricks
