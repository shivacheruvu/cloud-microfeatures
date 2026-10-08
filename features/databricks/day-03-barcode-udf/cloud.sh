#!/usr/bin/env bash
# Cloud run (GitHub Actions): create the Python UDF in a scratch schema, run the barcode audit on a Databricks
# serverless SQL warehouse, check it equals the local result, then drop the UDF and schema.
set -euo pipefail
pip install -q duckdb
python barcode_audit.py --databricks
