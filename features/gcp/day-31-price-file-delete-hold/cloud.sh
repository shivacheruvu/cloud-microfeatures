#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): an incident hold on a short-lived price-file bucket with Cloud Storage's hard
# delete pause (softDeletePolicy.hardDeletePause, Google Cloud release notes 2026-11-04), driven through the Cloud
# Storage JSON API with the runner's gcloud token: upload 7 price files, let a "bad cleanup" delete 5, pause hard
# deletion, restore the 4 good files from their soft-deleted generations, leave the draft, lift the pause, and check
# every step against the local plan (result.json). The bucket is deleted on exit (soft delete cleared first), and
# leftovers from an interrupted earlier run are deleted first. Cost: 7 tiny objects for a few seconds, ~40 API calls.
# price_hold.py redacts its own errors; everything it prints also goes through the same filter as the other days.
set -euo pipefail
: "${GCP_PROJECT_ID:?}"
redact() { sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/cmf-d31-[a-z0-9-]+/<bucket>/g' -e 's/[0-9]{6,}/<number>/g'; }
python price_hold.py --cloud 2>&1 | redact
