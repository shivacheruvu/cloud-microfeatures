#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived US dataset, create day 44's products table with a
# GENERATED ALWAYS AS (AI.EMBED(description, model => 'embeddinggemma-300m')) STORED OPTIONS (asynchronous = TRUE)
# column, wait until BigQuery has embedded all 12 rows, then answer the 9 shopper queries with AI.SEARCH and its
# rerank option (GA, 2026-11-17): vector search picks the top 5 candidates, the ranking model reorders them. The
# call form is probed first (forms in aisle_search_rerank.py, "pasta sauce" query) and the first one BigQuery accepts
# runs all 9 queries; the result is checked against the answer key. If no form is accepted, every form's error is
# annotated. The dataset is deleted on exit; tables also expire after an hour. Errors are annotations with the
# project ID redacted (logs are public). Billing: on-demand queries on 12 rows (10 MiB minimum each, capped; a query
# that asks for a higher minimum gets it, at most 1 GiB), the background embedding jobs' slots, and about 10 ranking
# API calls of 5 records (list price $1 per 1,000).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
STAMP=$(date +%s)
DS="mf_day46_${STAMP}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; ERR="$DIR/err.txt"
CAP=20971520
MAX_CAP=1073741824
POLL=20; WAIT_MAX=900
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c "${1:-900}"; }
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap cleanup EXIT

# Reranking calls the Vertex AI ranking API (Discovery Engine), enabled by day 45; enable it if it is ever off.
if [ -z "$(gcloud services list --enabled --filter='config.name=discoveryengine.googleapis.com' --format='value(config.name)' 2>/dev/null)" ]; then
  if gcloud services enable discoveryengine.googleapis.com --quiet >/dev/null 2>"$ERR"; then
    echo "::notice title=Google Cloud day 46::enabled the Discovery Engine API (discoveryengine.googleapis.com) for reranking"
    sleep 60
  else
    echo "::error title=Could not enable the Discovery Engine API::$(tail -c 600 "$ERR" | redact)"; exit 1
  fi
fi

if ! bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier \
     "${GCP_PROJECT_ID}:${DS}" > "$ERR" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$ERR")"; exit 1
fi

# run_query OUTFILE PART: SQL on stdin (a leading "--" comment would be read as a flag); retry transient errors
run_query() {
  local out="$1"
  python aisle_search_rerank.py --sql "$2" "$DS" > "$SQL"
  for wait in 20 40 0; do
    if bq --quiet --project_id="$GCP_PROJECT_ID" --location=US --format=json \
         query --nouse_legacy_sql --nouse_cache --maximum_bytes_billed="$CAP" --max_rows=1000 < "$SQL" >"$out" 2>"$ERR"; then
      return 0
    fi
    local n; n=$(cat "$ERR" "$out" 2>/dev/null | grep -oE "[0-9]+ or higher required" | grep -oE "^[0-9]+" | head -1)
    if [ -n "$n" ] && [ "$n" -le "$MAX_CAP" ] && [ "$n" -gt "$CAP" ]; then
      echo "::notice title=Google Cloud day 46 billing::$2 needs maximum_bytes_billed of at least $((n / 1048576)) MiB; re-running with that cap"
      CAP=$n; continue
    fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|not ready|try again" "$out" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 46 $1 failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }

run_query "$DIR/setup.json" setup || fail "CREATE TABLE with a generated AI.EMBED column" "$DIR/setup.json"

START=$SECONDS
while :; do
  run_query "$DIR/status.json" status || fail "status query" "$DIR/status.json"
  MSG=$(python aisle_search_rerank.py --status "$DIR/status.json" 12); RC=$?
  [ $RC = 0 ] && { echo "::notice title=Google Cloud day 46 embeddings::background embedding finished after $((SECONDS - START))s: $MSG"; break; }
  [ $RC = 1 ] && { echo "$MSG" | grep "::error"; exit 1; }
  if [ $((SECONDS - START)) -ge $WAIT_MAX ]; then
    echo "::error title=Day 46 embeddings::BigQuery had not embedded the rows after ${WAIT_MAX}s ($MSG)"; exit 1
  fi
  sleep $POLL
done

FORM=""; TRIED=""
for n in 1 2 3 4; do
  if run_query "$DIR/probe.json" "probe:$n"; then FORM=$n; break; fi
  TRIED="$TRIED [form $n] $( (tail -c 300 "$ERR"; tail -c 300 "$DIR/probe.json") | redact 300)"
done
if [ -z "$FORM" ]; then
  echo "::error title=Day 46 AI.SEARCH rerank refused in every call form::$(echo "$TRIED" | tail -c 950)"
  exit 1
fi
[ -n "$TRIED" ] && echo "::notice title=Google Cloud day 46 call form::form $FORM accepted; earlier forms refused:$(echo "$TRIED" | tail -c 700)"

run_query "$DIR/search.json" "search:$FORM" || fail "AI.SEARCH rerank for all queries (form $FORM)" "$DIR/search.json"
python aisle_search_rerank.py --compare "$DIR/search.json" "$FORM"
