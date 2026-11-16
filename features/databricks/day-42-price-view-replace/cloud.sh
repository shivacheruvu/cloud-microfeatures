#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits view_replace.py as a one-off serverless job
# (environment version 6) that creates a governed view in a scratch schema (comment, two tags, SELECT grant to
# account users), replaces its definition with CREATE OR REPLACE VIEW (Databricks SQL 2026.35) and checks from
# information_schema that the comment, tags and grant survived; the result must equal the local reference
# (result.json). The workspace folder, volume and schema are deleted on exit. Hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
