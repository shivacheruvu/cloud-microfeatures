#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): create a Cloud Run job (1 vCPU, 512 MiB) on Google's Node.js 26 runtime base
# image (nodejs26, Preview 2026-07-27), execute it once: job26.js computes delivery promises across the end of
# daylight saving time with Temporal, totals the order lines with Math.sumPrecise and matches catalog names with
# RegExp.escape. The DAY35_RESULT log line is read from Cloud Logging and must equal the local Python reference
# (result.json) and report Node.js 26. The job is deleted on exit (leftover cmf-d35-* jobs are deleted first).
# Cost: one task of a few seconds, Cloud Run free tier.
set -euo pipefail
: "${GCP_PROJECT_ID:?}"
redact() { sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[A-Za-z0-9._-]+@[A-Za-z0-9.-]+/<account>/g' -e 's/[0-9]{6,}/<number>/g'; }
python slots.py --cloud 2>&1 | redact
exit "${PIPESTATUS[0]}"
