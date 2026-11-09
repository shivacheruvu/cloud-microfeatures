#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits price_patterns.py as a one-off serverless job task on
# environment version 6: it runs two MATCH_RECOGNIZE queries (Beta, Databricks SQL 2026-06-30) - temporary price
# reductions per SKU with PATTERN (strt down+ (flat | down)* up+) and shopping trips per loyalty card with
# PATTERN (strt same_trip*) - and checks every row equals the local reference (result.json). The workspace folder,
# scratch volume and schema are deleted on exit. The workspace hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
