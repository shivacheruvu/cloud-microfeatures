#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived US dataset, create a products table whose description
# embedding is a GENERATED ALWAYS AS (AI.EMBED(description, model => 'embeddinggemma-300m')) STORED column with
# OPTIONS (asynchronous = TRUE): BigQuery autonomous embedding generation (GA, 2026-06-17). Then:
#   1. insert 11 products and poll until BigQuery's background jobs have embedded all of them (nothing is written to
#      the embedding column by this script; the built-in model runs on BigQuery slots, no connection, no Vertex AI);
#   2. insert a late product (oat milk) and poll again until the background job embeds it too;
#   3. AI.SEARCH for 9 shopper queries (falls back to ML.DISTANCE over the same column if AI.SEARCH is refused) and
#      check them against the answer key in aisle_finder.py.
# The dataset is deleted on exit; tables also expire after an hour. Errors are annotations with the project ID
# redacted (logs are public). Billing: on-demand DML / queries on 12 rows (10 MiB minimum each, capped; a search query
# that asks for a higher minimum gets it, at most 1 GiB) plus the background embedding jobs' slots.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
STAMP=$(date +%s)
DS="mf_day44_${STAMP}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; ERR="$DIR/err.txt"
CAP=20971520
MAX_CAP=1073741824
POLL=20; WAIT_MAX=900   # seconds per fill
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap cleanup EXIT

if ! bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier \
     "${GCP_PROJECT_ID}:${DS}" > "$ERR" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$ERR")"; exit 1
fi

# run_query OUTFILE PART: SQL on stdin (a leading "--" comment would be read as a flag); retry transient errors
run_query() {
  local out="$1"
  python aisle_finder.py --sql "$2" "$DS" > "$SQL"
  for wait in 20 40 0; do
    if bq --quiet --project_id="$GCP_PROJECT_ID" --location=US --format=json \
         query --nouse_legacy_sql --nouse_cache --maximum_bytes_billed="$CAP" --max_rows=1000 < "$SQL" >"$out" 2>"$ERR"; then
      return 0
    fi
    local n; n=$(cat "$ERR" "$out" 2>/dev/null | grep -oE "[0-9]+ or higher required" | grep -oE "^[0-9]+" | head -1)
    if [ -n "$n" ] && [ "$n" -le "$MAX_CAP" ] && [ "$n" -gt "$CAP" ]; then
      echo "::notice title=Google Cloud day 44 billing::$2 needs maximum_bytes_billed of at least $((n / 1048576)) MiB; re-running with that cap"
      CAP=$n; continue
    fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|not ready|try again" "$out" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 44 $1 failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }

# wait_embedded EXPECTED LABEL: poll the status query until EXPECTED rows carry an embedding
wait_embedded() {
  local start=$SECONDS rc msg
  while :; do
    run_query "$DIR/status.json" status || fail "status query" "$DIR/status.json"
    msg=$(python aisle_finder.py --status "$DIR/status.json" "$1"); rc=$?
    [ $rc = 0 ] && { echo "::notice title=Google Cloud day 44 $2::background embedding finished after $((SECONDS - start))s: $msg"; return 0; }
    [ $rc = 1 ] && { echo "$msg" | grep "::error" ; exit 1; }
    if [ $((SECONDS - start)) -ge $WAIT_MAX ]; then
      echo "::error title=Day 44 $2::BigQuery had not embedded the rows after ${WAIT_MAX}s ($msg)"
      exit 1
    fi
    sleep $POLL
  done
}

run_query "$DIR/setup.json" setup || fail "CREATE TABLE with a generated AI.EMBED column" "$DIR/setup.json"
wait_embedded 11 "first fill"
run_query "$DIR/fresh.json" fresh || fail "late INSERT" "$DIR/fresh.json"
wait_embedded 12 "late row"

if ! run_query "$DIR/search.json" search; then
  echo "::warning title=Day 44 AI.SEARCH refused::$( (tail -c 400 "$ERR"; tail -c 400 "$DIR/search.json") | redact); ranking with ML.DISTANCE over the same generated column instead"
  run_query "$DIR/search.json" search_fallback || fail "search (AI.SEARCH and ML.DISTANCE fallback)" "$DIR/search.json"
fi
python aisle_finder.py --compare "$DIR/search.json"
