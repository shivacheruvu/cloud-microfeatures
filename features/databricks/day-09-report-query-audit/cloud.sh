#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse, parse the eight saved report queries with
# parse_sql() and check the verdicts and table lists against the local DuckDB parse. Read-only: nothing is created,
# and parse_sql never runs the queries it parses.
set -euo pipefail
python audit.py --databricks
