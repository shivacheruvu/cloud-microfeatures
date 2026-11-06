#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits price_path.py as a one-off serverless job task on
# environment version 6: it creates two price-book schemas, runs the same unqualified basket report under two SET PATH
# settings (Databricks SQL 2026.43 / Databricks Runtime 19+) and checks totals, current_path() and the refusals equal
# the local reference (result.json). The workspace folder, scratch volume and all three schemas are deleted on exit.
# The workspace hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
