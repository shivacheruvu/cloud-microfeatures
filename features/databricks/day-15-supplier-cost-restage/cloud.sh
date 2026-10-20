#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse (Free Edition compatible), stage a supplier cost
# file and its corrected re-delivery with CREATE OR REPLACE TEMP TABLE (Databricks SQL 2026.15) inside one SQL script,
# and check the margin result against the local Python one. Temporary tables end with the session: nothing remains.
set -euo pipefail
python restage.py --databricks
