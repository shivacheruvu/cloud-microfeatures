#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse (Free Edition compatible), load a scratch table,
# run one "daily close" report per store through the Statement Execution API with statement-level `query_tags`
# (store, report, feature), check each result equals the local DuckDB run, then read the tags back from
# system.query.history and roll them up per store. Drops the table and schema on exit.
set -euo pipefail
python store_query_tags.py --databricks
