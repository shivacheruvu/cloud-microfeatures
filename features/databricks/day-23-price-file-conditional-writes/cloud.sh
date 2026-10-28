#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Creates a scratch schema and managed volume through the Unity
# Catalog REST API, then plays the shared price-file scenario against Files API 3.0 (Beta, 2026-10-23) with
# conditional uploads (If-None-Match: *, If-Match: <ETag>) and a conditional delete, and checks every step's outcome
# and the final file equal the local run. Volume and schema are deleted on exit. The workspace hostname is never printed.
set -euo pipefail
python price_file.py --databricks
