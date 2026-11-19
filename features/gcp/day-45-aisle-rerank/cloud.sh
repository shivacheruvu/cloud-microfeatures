#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived US dataset, load day 44's 12-product catalog as a plain table
# (no embeddings) and answer the 9 shopper queries with AI.RANK (GA, 2026-11-17), which reorders a table's rows by how
# well the text in a column answers a query. The call form is probed first (cloud.sh tries the forms in
# aisle_rerank.py in order, with the "pasta sauce" query) and the first one BigQuery accepts ranks all 9 queries; the
# result is checked against the day-44 answer key. If no form is accepted, every form's error is annotated.
# The dataset is deleted on exit; tables also expire after an hour. Errors are annotations with the project ID
# redacted (logs are public). Billing: on-demand queries on 12 rows (10 MiB minimum each, capped; a query that asks
# for a higher minimum gets it, at most 1 GiB) plus AI.RANK's model calls (9 queries x 12 rows).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
STAMP=$(date +%s)
DS="mf_day45_${STAMP}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; ERR="$DIR/err.txt"
CAP=20971520
MAX_CAP=1073741824
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c "${1:-900}"; }
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap cleanup EXIT

if ! bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier \
     "${GCP_PROJECT_ID}:${DS}" > "$ERR" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$ERR")"; exit 1
fi

# run_query OUTFILE PART: SQL on stdin (a leading "--" comment would be read as a flag); retry transient errors
run_query() {
  local out="$1"
  python aisle_rerank.py --sql "$2" "$DS" > "$SQL"
  for wait in 20 40 0; do
    if bq --quiet --project_id="$GCP_PROJECT_ID" --location=US --format=json \
         query --nouse_legacy_sql --nouse_cache --maximum_bytes_billed="$CAP" --max_rows=1000 < "$SQL" >"$out" 2>"$ERR"; then
      return 0
    fi
    local n; n=$(cat "$ERR" "$out" 2>/dev/null | grep -oE "[0-9]+ or higher required" | grep -oE "^[0-9]+" | head -1)
    if [ -n "$n" ] && [ "$n" -le "$MAX_CAP" ] && [ "$n" -gt "$CAP" ]; then
      echo "::notice title=Google Cloud day 45 billing::$2 needs maximum_bytes_billed of at least $((n / 1048576)) MiB; re-running with that cap"
      CAP=$n; continue
    fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|not ready|try again" "$out" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 45 $1 failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }

run_query "$DIR/setup.json" setup || fail "CREATE TABLE products" "$DIR/setup.json"

FORM=""; TRIED=""
for n in 1 2 3; do
  if run_query "$DIR/probe.json" "probe:$n"; then FORM=$n; break; fi
  TRIED="$TRIED [form $n] $( (tail -c 300 "$ERR"; tail -c 300 "$DIR/probe.json") | redact 300)"
done
if [ -z "$FORM" ]; then
  echo "::error title=Day 45 AI.RANK refused in every call form::$(echo "$TRIED" | tail -c 950)"
  exit 1
fi
[ -n "$TRIED" ] && echo "::notice title=Google Cloud day 45 call form::form $FORM accepted; earlier forms refused:$(echo "$TRIED" | tail -c 700)"

run_query "$DIR/rank.json" "rank:$FORM" || fail "AI.RANK for all queries (form $FORM)" "$DIR/rank.json"
python aisle_rerank.py --compare "$DIR/rank.json" "$FORM"
