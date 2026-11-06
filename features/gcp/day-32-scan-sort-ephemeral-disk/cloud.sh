#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): create a Cloud Run job (python:3.12-slim, 1 vCPU, 512 MiB) with a 1 GiB
# ephemeral disk mounted at /mnt/spill (Cloud Run ephemeral disk, GA 2026-11-04), execute it once: it external-sorts a
# 6-million-line scan export in 6 runs spilled to the disk and rolls it up per SKU. The DAY32_RESULT log line is read
# from Cloud Logging and must equal the local reference (result.json), and the mount must be a ~1 GiB ext4 disk with
# nothing left on it. The job is deleted on exit (leftover cmf-d32-* jobs are deleted first). Cost: one task of about
# a minute (1 vCPU, 512 MiB) and 1 GiB of disk for that minute, inside the Cloud Run free tier.
set -euo pipefail
: "${GCP_PROJECT_ID:?}"
redact() { sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[A-Za-z0-9._-]+@[A-Za-z0-9.-]+/<account>/g' -e 's/[0-9]{6,}/<number>/g'; }
python spill_sort.py --cloud 2>&1 | redact
