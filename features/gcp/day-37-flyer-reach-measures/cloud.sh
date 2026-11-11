#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived US dataset, create Brands / Products / Flyers / FlyerSlots tables
# and a property graph whose node tables carry MEASURE(...) properties (BigQuery Graph measures, GA 2026-11-10). Then:
#   1. GRAPH_EXPAND + AGG: per brand, naive SUM over the flattened rows next to AGG over the measures; every column
#      must equal the local model (AGG counts each product / flyer once, SUM counts it once per flyer slot).
#   2. Guard: SUM over a measure column must be refused ("only permitted in the AGG function").
# Runs on on-demand pricing (graph queries allowed there since 2026-11-06); cache off, as the docs advise for
# GRAPH_EXPAND. The dataset is deleted on exit; tables also expire after an hour.
# Billing: four tiny INSERTs and two queries, each at the 10 MiB minimum and capped at 20 MiB (well under a cent).
# Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
STAMP=$(date +%s)
DS="mf_day37_${STAMP}"
FULL="${GCP_PROJECT_ID}.${DS}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; ERR="$DIR/err.txt"
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap cleanup EXIT

if ! bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier \
     "${GCP_PROJECT_ID}:${DS}" > "$ERR" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$ERR")"; exit 1
fi

# run_query OUTFILE PART [GRAPH_REF]: SQL on stdin (a leading "--" comment would be read as a flag); retry transient errors
run_query() {
  local out="$1"; shift
  python flyer_measures.py --sql "$1" "$FULL" ${2:+"$2"} > "$SQL"
  for wait in 20 40 0; do
    if bq --quiet --project_id="$GCP_PROJECT_ID" --location=US --format=json query --nouse_legacy_sql --nouse_cache \
         --maximum_bytes_billed=20971520 --max_rows=1000 < "$SQL" >"$out" 2>"$ERR"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|not ready|try again" "$out" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 37 $1 failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }

run_query "$DIR/setup.json" setup || fail "tables + CREATE PROPERTY GRAPH with MEASURE" "$DIR/setup.json"

# GRAPH_EXPAND takes the graph name as a string: try project.dataset.graph, then dataset.graph (default project)
REF="${FULL}.FlyerGraph"
if ! run_query "$DIR/expand.json" expand "$REF"; then
  REF="${DS}.FlyerGraph"
  run_query "$DIR/expand.json" expand "$REF" || fail "GRAPH_EXPAND + AGG query" "$DIR/expand.json"
fi
python flyer_measures.py --compare "$DIR/expand.json" || exit 1

if run_query "$DIR/guard.json" guard "$REF"; then
  echo "::error title=Day 37 guard::SUM over a MEASURE column was accepted; expected it to be refused"; exit 1
elif grep -qiE "only permitted in the AGG|MEASURE" "$ERR" "$DIR/guard.json"; then
  echo "::notice title=Google Cloud day 37 guard::SUM over a measure was refused as documented: $( (cat "$ERR" "$DIR/guard.json") | grep -oiE "[^.:]*MEASURE[^.]*" | head -1 | redact)"
else
  fail "guard query (unexpected error)" "$DIR/guard.json"
fi
