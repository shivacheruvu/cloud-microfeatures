#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery query translates 12 English shelf tags to Spanish with AI.TRANSLATE
# (GA 2026-10-22, default NMT model, return_details => TRUE). The tags are inlined in the SQL, so nothing is created
# and nothing needs cleaning up. Every row must come back with status OK; prices, sizes and counts that changed in
# translation are reported as warnings (see shelf_tags.py). AI.TRANSLATE calls the Cloud Translation API; the script
# enables it if it is off (enabling is free; NMT is billed per character, ~400 characters here).
# Errors are printed with the project ID and any long number (project numbers) redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
trap 'rm -f "$SQL" "$OUT" "$ERR"' EXIT
python shelf_tags.py --sql > "$SQL"
if [ -z "$(gcloud services list --enabled --filter='config.name=translate.googleapis.com' --format='value(config.name)' 2>/dev/null)" ]; then
  if gcloud services enable translate.googleapis.com --quiet >/dev/null 2>"$ERR"; then
    echo "::notice title=Google Cloud day 28::enabled the Cloud Translation API (translate.googleapis.com) for AI.TRANSLATE"
  else
    echo "::error title=Could not enable the Cloud Translation API::$(tail -c 600 "$ERR" | redact)"; exit 1
  fi
fi
for wait in 30 60 0; do
  # SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then
    python shelf_tags.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|API is not enabled|has not been used|resource exhausted|429" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=AI.TRANSLATE query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
exit 1
