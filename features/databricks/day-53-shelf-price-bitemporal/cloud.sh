#!/usr/bin/env bash
# Cloud run (GitHub Actions): on Databricks Free Edition, create a scratch schema with a price_feed Delta table and a
# serverless Lakeflow pipeline (PREVIEW channel) whose streaming table shelf_prices is fed by AUTO CDC ... SEQUENCE BY
# effective_day SYSTEM SEQUENCE BY feed_batch STORED AS BITEMPORAL (Beta, DBR 17.3+). Two rounds of feed batches are
# appended, one pipeline update runs after each, and every as-of answer (SKU x effective day x feed batch) read from the
# target with the two-axis filter must equal the local Python/DuckDB reference. An inactive workspace or an unavailable
# feature is a warning. Pipeline, workspace folder and schema are deleted on exit.
set -euo pipefail
python bitemporal.py --databricks
