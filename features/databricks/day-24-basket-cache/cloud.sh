#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits basket_cache.py as a one-off serverless job task on
# environment version 5, where df.cache() / df.persist() are supported (Public Preview, 2026-10-23), and checks the
# cached-DataFrame reports equal the local pyspark run (result.json). The workspace folder, scratch volume and schema
# are deleted on exit. The workspace hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
