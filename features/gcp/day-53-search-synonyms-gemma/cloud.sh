#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery query scores 256 (search term, catalog product) pairs with
# AI.SIMILARITY on embeddinggemma-300m (GA in AI.EMBED / AI.SIMILARITY, 2026-11-23); a second query scores the same
# pairs with gemini-embedding-2 for comparison (a failure there is only a warning). Searches and products are inlined
# in the SQL, so nothing is created and nothing needs cleaning up. AI.SIMILARITY calls the model through the Vertex AI
# API, which day 26 enables if needed. Errors are annotations with the project ID and long numbers redacted.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
DIR=$(mktemp -d); trap 'rm -rf "$DIR"' EXIT
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
LAST_ERR=""

if [ -z "$(gcloud services list --enabled --filter='config.name=aiplatform.googleapis.com' --format='value(config.name)' 2>/dev/null)" ]; then
  gcloud services enable aiplatform.googleapis.com --quiet >/dev/null 2>"$DIR/enable.txt" \
    || { echo "::error title=Could not enable the Vertex AI API::$(redact < "$DIR/enable.txt")"; exit 1; }
fi

run_query() {  # $1 = endpoint, $2 = output file; retries only BigQuery's transient errors
  local sql="$DIR/q$RANDOM.sql" err="$DIR/err$RANDOM.txt"
  python matcher.py --sql "$1" > "$sql"
  for wait in 20 60 0; do
    # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
    if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
         --max_rows=1000 < "$sql" >"$2" 2>"$err"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|429|has not been used" "$2" "$err"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  LAST_ERR=$( (tail -c 600 "$err"; tail -c 600 "$2") | redact)
  return 1
}

if ! run_query embeddinggemma-300m "$DIR/gemma.json"; then
  echo "::error title=AI.SIMILARITY (embeddinggemma-300m) query failed::${LAST_ERR}"
  exit 1
fi
if run_query gemini-embedding-2 "$DIR/gemini.json"; then
  python matcher.py --compare "$DIR/gemma.json" "$DIR/gemini.json"
else
  echo "::warning title=AI.SIMILARITY (gemini-embedding-2) comparison failed::${LAST_ERR}"
  python matcher.py --compare "$DIR/gemma.json"
fi
