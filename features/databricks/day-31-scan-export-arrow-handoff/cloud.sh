#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits arrow_handoff.py as a one-off serverless job task on
# environment version 6, where createDataFrame() accepts pandas DataFrames with PyArrow ChunkedArray columns
# (serverless release 2026-08-17), and checks the schema, NULLs and lane totals equal the local reference
# (result.json). The workspace folder, scratch
# volume and schema are deleted on exit. The workspace hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
