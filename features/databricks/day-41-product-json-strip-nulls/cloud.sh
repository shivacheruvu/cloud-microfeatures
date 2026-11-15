#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits product_payloads.py as a one-off serverless job task on
# environment version 6: it parses 8 null-padded product payloads into VARIANT, slims them with variant_strip_nulls
# (serverless, Databricks Runtime 19 updates, 2026-09-01), measures bytes and nutriment completeness, and checks
# everything equals the local reference (result.json). The workspace folder, scratch volume and schema are deleted on
# exit. The workspace hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
