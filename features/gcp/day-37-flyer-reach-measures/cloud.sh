#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived US dataset, create Brands / Products / Flyers / FlyerSlots tables
# and a property graph whose node tables carry MEASURE(...) properties (BigQuery Graph measures, GA 2026-11-10). Then:
#   1. GRAPH_EXPAND + AGG: per brand, naive SUM over the flattened rows next to AGG over the measures; every column
#      must equal the local model (AGG counts each product / flyer once, SUM counts it once per flyer slot).
#   2. Guard: SUM over a measure column must be refused ("only permitted in the AGG function").
# Runs on on-demand pricing (graph queries allowed there since 2026-11-06); cache off, as the docs advise for
# GRAPH_EXPAND. The dataset is deleted on exit; tables also expire after an hour.
# Billing: four tiny INSERTs and two queries. INSERTs bill the 10 MiB minimum (20 MiB cap); the graph query needs a
# higher minimum, so its cap is raised to what BigQuery reports, at most 1 GiB (under a cent either way).
# Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
STAMP=$(date +%s)
DS="mf_day37_${STAMP}"
FULL="${GCP_PROJECT_ID}.${DS}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; ERR="$DIR/err.txt"
CAP=20971520            # 20 MiB; raised once (to at most 1 GiB) if BigQuery says a graph query needs more
MAX_CAP=1073741824
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
         --maximum_bytes_billed="$CAP" --max_rows=1000 < "$SQL" >"$out" 2>"$ERR"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|not ready|try again" "$out" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 37 $1 failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }

run_query "$DIR/setup.json" setup || fail "tables + CREATE PROPERTY GRAPH with MEASURE" "$DIR/setup.json"

# needed_cap OUT: the "N or higher required" from a bytes-billed refusal, if it is within MAX_CAP
needed_cap() {
  local n; n=$(cat "$ERR" "$1" 2>/dev/null | grep -oE "[0-9]+ or higher required" | grep -oE "^[0-9]+" | head -1)
  [ -n "$n" ] && [ "$n" -le "$MAX_CAP" ] && echo "$n"
}
# GRAPH_EXPAND takes the graph name as a string: try project.dataset.graph, then dataset.graph (default project).
# The 2026-11-10 run was refused at the 20 MiB cap (graph queries bill a higher minimum), so raise the cap once to
# exactly what BigQuery asks for, up to 1 GiB (about $0.006 on demand), and report it.
REF="${FULL}.FlyerGraph"
if ! run_query "$DIR/expand.json" expand "$REF"; then
  if NEED=$(needed_cap "$DIR/expand.json"); then
    echo "::notice title=Google Cloud day 37 billing::GRAPH_EXPAND needs maximum_bytes_billed of at least $NEED bytes ($((NEED / 1048576)) MiB); re-running with that cap"
    CAP=$NEED
  else
    REF="${DS}.FlyerGraph"
  fi
  run_query "$DIR/expand.json" expand "$REF" || {
    if [ "$REF" = "${FULL}.FlyerGraph" ]; then REF="${DS}.FlyerGraph"; run_query "$DIR/expand.json" expand "$REF"; else false; fi
  } || fail "GRAPH_EXPAND + AGG query" "$DIR/expand.json"
fi
python flyer_measures.py --compare "$DIR/expand.json" || exit 1

if run_query "$DIR/guard.json" guard "$REF"; then
  echo "::error title=Day 37 guard::SUM over a MEASURE column was accepted; expected it to be refused"; exit 1
elif grep -qiE "only permitted in the AGG|MEASURE" "$ERR" "$DIR/guard.json"; then
  echo "::notice title=Google Cloud day 37 guard::SUM over a measure was refused as documented: $( (cat "$ERR" "$DIR/guard.json") | grep -oiE "[^.:]*MEASURE[^.]*" | head -1 | redact)"
else
  fail "guard query (unexpected error)" "$DIR/guard.json"
fi
