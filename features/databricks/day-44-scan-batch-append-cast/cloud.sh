#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits append_cast.py as a one-off serverless job
# (environment version 6) that creates a Delta scan table in a scratch schema and appends two store batches with
# narrower column types (SMALLINT / INT / DECIMAL(6,2) and DECIMAL(5,2) into INT / BIGINT / DECIMAL(10,2)): one with
# writeTo().append(), one with write.mode("append").saveAsTable(). Since the 2026-07-06 serverless release these cast
# type-compatible columns instead of failing with DELTA_FAILED_TO_MERGE_FIELDS. The schema, rows and per-store totals
# must equal the local reference (result.json). The workspace folder, volume and schema are deleted on exit.
# Hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
