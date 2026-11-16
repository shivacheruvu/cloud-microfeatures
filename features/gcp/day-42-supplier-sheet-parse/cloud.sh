#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): upload the one-page supplier price sheet (data/harbor_farms_w46.pdf) to a
# short-lived Cloud Storage bucket (US, uniform access, public access prevented, no soft delete), parse it with one
# BigQuery query using AI.PARSE_DOCUMENT (GA 2026-11-02, layout-v1 blocks) and check the blocks against the answer key
# (see supplier_sheet.py). AI.PARSE_DOCUMENT calls the Document AI layout parser, so the script makes sure the
# Document AI API is on; on its first run BigQuery creates one processor, bq-created-layout-parser, in the project
# (no idle fee; it is kept so later runs don't recreate it). The bucket and the PDF are deleted on exit.
# Bucket names and errors are never printed with the project ID (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
BUCKET="mf-day42-${GITHUB_RUN_ID:-local}-$(date +%s)-${RANDOM}"
URI="gs://${BUCKET}/supplier/harbor_farms_w46.pdf"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e "s/${BUCKET}/<bucket>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
cleanup() { gcloud storage rm -r "gs://${BUCKET}" --quiet >/dev/null 2>&1 || true; rm -f "$SQL" "$OUT" "$ERR"; }
trap cleanup EXIT
if [ -z "$(gcloud services list --enabled --filter='config.name=documentai.googleapis.com' --format='value(config.name)' 2>/dev/null)" ]; then
  if gcloud services enable documentai.googleapis.com --quiet >/dev/null 2>"$ERR"; then
    echo "::notice title=Google Cloud day 42::enabled the Document AI API (documentai.googleapis.com) for AI.PARSE_DOCUMENT"
    sleep 30
  else
    echo "::error title=Could not enable the Document AI API::$(tail -c 600 "$ERR" | redact)"; exit 1
  fi
fi
if ! gcloud storage buckets create "gs://${BUCKET}" --location=US --uniform-bucket-level-access \
     --public-access-prevention --soft-delete-duration=0 --quiet > "$OUT" 2>&1; then
  echo "::error title=Create bucket failed::$(redact < "$OUT")"; exit 1
fi
if ! gcloud storage cp data/harbor_farms_w46.pdf "$URI" --quiet > "$OUT" 2>&1; then
  echo "::error title=Upload failed::$(redact < "$OUT")"; exit 1
fi
python supplier_sheet.py --sql "$URI" > "$SQL"
RETRY="internal error|transient|backendError|API is not enabled|has not been used|SERVICE_DISABLED|not ready|UNAVAILABLE|resource exhausted|429|processor"
for wait in 60 120 0; do
  # SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=10 < "$SQL" >"$OUT" 2>"$ERR"; then
    # a failed parse comes back as a row with an errors array; retry those while the API / processor settles
    if [ "$wait" != 0 ] && grep -q '"errors":"\[' "$OUT" && grep -qiE "$RETRY" "$OUT"; then
      echo "::warning title=Document AI not ready::retrying in ${wait}s"; sleep "$wait"; continue
    fi
    sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e "s/${BUCKET}/<bucket>/g" "$OUT" > "$ERR"
    python supplier_sheet.py --compare "$ERR"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "$RETRY" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery / Document AI transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=Day 42 AI.PARSE_DOCUMENT query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
exit 1
