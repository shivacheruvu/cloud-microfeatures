#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse, pick each store's current shelf price and each
# aisle's top 2 sellers with QUALIFY (Databricks SQL 2026.20), and check every row against the local Python result.
# Read-only.
set -euo pipefail
python shelf.py --databricks
