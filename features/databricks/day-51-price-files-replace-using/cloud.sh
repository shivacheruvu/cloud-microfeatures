#!/usr/bin/env bash
# Cloud run (GitHub Actions): on Databricks Free Edition, create a scratch schema with a price_drops Delta table and a
# serverless Lakeflow pipeline (PREVIEW channel) whose streaming table shelf_prices is fed by
# FLOW REPLACE USING (store_id) SEQUENCE BY file_version (Beta, Databricks Runtime 18.2+). Three delivery rounds are
# appended, one pipeline update runs after each, and shelf_prices must equal the local Python/DuckDB state every time.
# An inactive workspace or an unavailable Beta is a warning. Pipeline, workspace folder and schema are deleted on exit.
set -euo pipefail
python price_files.py --databricks
