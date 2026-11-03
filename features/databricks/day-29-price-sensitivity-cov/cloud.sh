#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits price_cov.py as a one-off serverless job task on
# environment version 6, where pandas API on Spark has DataFrameGroupBy.cov (serverless release 2026-08-17), and
# checks the per-store covariance matrices equal the local reference (result.json). The workspace folder, scratch
# volume and schema are deleted on exit. The workspace hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
