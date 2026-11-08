#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): create a Cloud Run job (1 vCPU, 512 MiB) on Google's Python 3.15 runtime base
# image (python315, Preview 2026-11-02), execute it once: job315.py resolves shelf prices through chain/region/store
# layers, dedupes promo rules with frozendict and totals 10 baskets. The DAY34_RESULT log line is read from Cloud
# Logging and must equal the local reference (result.json) and report Python 3.15 with UTF-8 mode on. The job is
# deleted on exit (leftover cmf-d34-* jobs are deleted first). Cost: one task of a few seconds, Cloud Run free tier.
set -euo pipefail
: "${GCP_PROJECT_ID:?}"
redact() { sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[A-Za-z0-9._-]+@[A-Za-z0-9.-]+/<account>/g' -e 's/[0-9]{6,}/<number>/g'; }
python rollup.py --cloud 2>&1 | redact
exit "${PIPESTATUS[0]}"
