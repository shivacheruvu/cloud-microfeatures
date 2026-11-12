#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits restock_cases.py as a one-off serverless job task on
# environment version 6: it creates the temporary SQL UDF cases_to_pick and passes it straight into the range
# table-valued function (scalar UDFs as TVF arguments, Databricks SQL 2026.32) to build the night crew's case pick
# list, builds the same list with explode(sequence(...)), and checks both equal the local reference (result.json).
# The workspace folder, scratch volume and schema are deleted on exit. The hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
