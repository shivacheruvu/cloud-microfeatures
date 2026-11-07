#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits scan_payloads.py as a one-off serverless job task on
# environment version 6: it writes a 14-row scan export with three malformed VARIANT payloads into a scratch volume,
# quarantines them with is_valid_variant (serverless, Databricks Runtime 19 updates), reports per lane from the clean
# rows and checks everything equals the local reference (result.json). The workspace folder, scratch volume and
# schema are deleted on exit. The workspace hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
