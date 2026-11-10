#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Creates a one-task job whose task parameters are dynamic value
# references with date arithmetic (Lakeflow Jobs, Beta 2026-11-09), e.g. {{(job.start_time + 3.days).iso_date}},
# runs it once on serverless and checks every resolved value equals the local resolver for that run's start time.
# The job, workspace folder, scratch volume and schema are deleted on exit. Hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
