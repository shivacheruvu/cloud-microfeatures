#!/usr/bin/env bash
# Cloud run (GitHub Actions): on Databricks Free Edition, create a scratch schema with an inventory_snapshots Delta
# table and a serverless Lakeflow pipeline (PREVIEW channel) whose streaming table inventory_history is fed by
# AUTO CDC ... FROM SNAPSHOT ... WITH VERSION ... STORED AS SCD TYPE 2 (Databricks Runtime 18.3+). Two rounds of
# nightly snapshots are appended, one pipeline update runs after each, and the history must equal the local
# Python/DuckDB reference every time. An inactive workspace or an unavailable feature is a warning.
# Pipeline, workspace folder and schema are deleted on exit.
set -euo pipefail
python snapshot_cdc.py --databricks
