#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse (Free Edition compatible), create a scratch
# table and a metric view with a `markdown` PARAMETER (Public Preview), query it as a table-valued function at four
# markdown levels and once with the default, check every result equals the local DuckDB run, then drop the view,
# table and schema.
set -euo pipefail
python markdown_what_if.py --databricks
