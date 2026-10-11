#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse, create scratch tables, run INSERT INTO ...
# REPLACE USING and REPLACE ON twice each, check every snapshot equals the local result, then drop tables and schema.
set -euo pipefail
pip install -q duckdb
python reload.py --databricks
