#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse (Free Edition compatible), create two managed
# tables with catalog commits, book six supplier deliveries as one BEGIN ATOMIC transaction each (multi-statement
# transactions, GA 2026-10-14), and check which committed, which rolled back and the final stock against the local
# DuckDB transactions. The scratch schema and tables are dropped on exit.
set -euo pipefail
python receipts.py --databricks
