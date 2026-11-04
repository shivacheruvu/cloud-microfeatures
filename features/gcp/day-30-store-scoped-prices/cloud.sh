#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): create a short-lived US dataset with a 24-row store price table and ONE logical
# view that filters by SECURE_CONTEXT('store_id') and hides unit cost unless SECURE_CONTEXT('role') = 'buyer'
# (Preview, 2026-11-02). Then query the view over the BigQuery REST API (jobs.query) as five callers, each passing
# only `secureContext.secureParameterEntries`, and check every answer equals the local reference (result.json). Also
# checks the documented failures: no context, a missing key, SECURE_CONTEXT outside a view. The dataset is dropped on
# exit (tables also expire after 2.4 h). Billing: ~10 queries at the 10 MiB minimum each, capped at 20 MiB.
# store_view.py redacts its own errors; everything it prints also goes through the same filter as the other days.
set -euo pipefail
: "${GCP_PROJECT_ID:?}"
redact() { sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g'; }
python store_view.py --cloud 2>&1 | redact
