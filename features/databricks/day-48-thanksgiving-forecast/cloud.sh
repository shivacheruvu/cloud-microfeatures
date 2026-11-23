#!/usr/bin/env bash
# Cloud run (GitHub Actions): on the workspace's serverless SQL warehouse (Statement Execution API), forecast
# 2026-11-15..30 for three Thanksgiving SKUs with ai_forecast version 2 (GA 2026-11-18) in three variants (plain;
# holiday_region 'US' + positive_only; + the planned promo as a covariate). Each result must have 16 days x 3 SKUs,
# ordered intervals and, with positive_only, no negative values; accuracy against the generator's held-out days is
# reported. Read-only: the data are inlined, nothing is created. Hostname and user name are never printed.
set -euo pipefail
python forecast.py --databricks
