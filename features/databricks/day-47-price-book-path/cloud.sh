#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed (SET PATH must hold across statements, so everything runs in one
# Spark session). Submits path_prices.py as a one-off serverless job (environment version 6, Databricks Runtime 19)
# that builds a live and a promo price book in two scratch schemas and prices one basket with an unqualified query
# under four SET PATH settings (SET PATH / current_path(), Databricks SQL 2026.43, 2026-10-19), plus two refused
# forms. Every total, current_path() string and error class must equal the local model. Workspace folder, volume and
# both schemas are deleted on exit. Hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
