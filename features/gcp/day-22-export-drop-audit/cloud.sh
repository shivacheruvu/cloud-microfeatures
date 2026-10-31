#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): create a short-lived Cloud Storage bucket (US, uniform access, public access
# prevented), upload the 8 store export files, run one BigQuery query that inventories them with OBJ.LIST (GA
# 2026-09-30) against the expected store x day calendar, and check the result equals the local run. The bucket and
# its files are deleted on exit. Bucket names and errors are never printed with the project ID (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
BUCKET="mf-day22-${GITHUB_RUN_ID:-local}-$(date +%s)-${RANDOM}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e "s/${BUCKET}/<bucket>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
cleanup() { gcloud storage rm -r "gs://${BUCKET}" --quiet >/dev/null 2>&1 || true; rm -f "$SQL" "$OUT" "$ERR"; }
trap cleanup EXIT
if ! gcloud storage buckets create "gs://${BUCKET}" --location=US --uniform-bucket-level-access \
     --public-access-prevention --soft-delete-duration=0 --quiet > "$OUT" 2>&1; then
  echo "::error title=Create bucket failed::$(redact < "$OUT")"; exit 1
fi
if ! gcloud storage cp data/exports/*.csv "gs://${BUCKET}/exports/" --quiet > "$OUT" 2>&1; then
  echo "::error title=Upload failed::$(redact < "$OUT")"; exit 1
fi
python export_audit.py --sql "${BUCKET}" > "$SQL"
for wait in 10 30 0; do
  # SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then
    python export_audit.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=OBJ.LIST query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
exit 1
