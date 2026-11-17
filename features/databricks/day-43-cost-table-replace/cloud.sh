#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits table_replace.py as a one-off serverless job
# (environment version 6) that creates a documented Delta table in a scratch schema (table comment, four column
# comments), reloads it with CREATE OR REPLACE TABLE ... AS SELECT naming no comments (serverless release 2026-07-06:
# existing table and column comments are preserved by default) and checks from information_schema that they survived;
# the result must equal the local reference (result.json). The workspace folder, volume and schema are deleted on exit.
# Hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
