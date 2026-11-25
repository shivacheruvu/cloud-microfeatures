#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery query groups 24 Thanksgiving-week shopper comments by department and
# asks AI.AGG for a typed digest per group with output_schema (top_issue STRING, sentiment STRING,
# any_out_of_stock BOOL, headline STRING; structured output from AI.AGG went GA 2026-10-14). Counts come from COUNT(*).
# The comments are inlined in the SQL, so nothing is created and nothing needs cleaning up. Every digest must be a usable
# STRUCT; agreement with the hand-labelled answer key is reported (see feedback_digest.py). AI.AGG calls Gemini
# through the Vertex AI API, which day 26 enabled; the script still makes sure it is on (a few short calls, under a cent).
# Errors are printed with the project ID and any long number (project numbers) redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
trap 'rm -f "$SQL" "$OUT" "$ERR"' EXIT
python feedback_digest.py --sql > "$SQL"
if [ -z "$(gcloud services list --enabled --filter='config.name=aiplatform.googleapis.com' --format='value(config.name)' 2>/dev/null)" ]; then
  if gcloud services enable aiplatform.googleapis.com --quiet >/dev/null 2>"$ERR"; then
    echo "::notice title=Google Cloud day 51::enabled the Vertex AI API (aiplatform.googleapis.com) for AI.AGG"
  else
    echo "::error title=Could not enable the Vertex AI API::$(tail -c 600 "$ERR" | redact)"; exit 1
  fi
fi
for wait in 30 60 0; do
  # SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then
    python feedback_digest.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|API is not enabled|has not been used|resource exhausted|429" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=Day 51 AI.AGG (output_schema) query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
exit 1
