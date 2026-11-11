#!/usr/bin/env bash
# Cloud run (GitHub Actions): two upstream receiving jobs and a downstream restock-plan job with a job completion
# trigger on both (Lakeflow Jobs, Beta 2026-11-03), serverless. Checks the trigger hands over exactly the two upstream
# runs. All jobs and the workspace folder are deleted on exit. Hostname, user name and IDs are never printed.
set -euo pipefail
python run_cloud.py
