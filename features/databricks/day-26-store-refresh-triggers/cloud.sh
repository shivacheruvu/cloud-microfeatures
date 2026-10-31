#!/usr/bin/env bash
# Cloud run (GitHub Actions): no compute. Creates a scratch schema (mf26_prices) with two managed volumes in the default
# catalog, then reconciles the three trigger versions of the store price-refresh job through the Jobs API 2.2 with a
# `triggers` array (multiple triggers for Lakeflow Jobs, Beta 2026-10-05): create, no-op re-apply, two full-array
# updates, two updates the API must refuse, delete. Every step's outcome and the triggers read back must equal the
# local run. The job is never run, and the job, volumes and schema are deleted on exit. The hostname is never printed.
set -euo pipefail
python triggers.py --databricks
