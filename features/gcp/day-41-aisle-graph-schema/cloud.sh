#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived US dataset, create Aisles / Products / ShelfScans and a property
# graph whose properties and MEASURE(...)s carry OPTIONS(description=...). Then:
#   1. CALL BQ.SHOW_GRAPH_EXPAND_SCHEMA(graph, var) (release note 2026-11-10): the reported GRAPH_EXPAND columns must
#      include every column of the local contract, with exactly the three measures flagged; descriptions are compared
#      where the JSON carries them.
#   2. GRAPH_EXPAND + AGG over the promised columns: per aisle, every number must equal the local model.
# If the graph DDL with OPTIONS is refused, it is created without them (warning) so the schema check still runs.
# The dataset is deleted on exit; tables also expire after an hour. Errors are annotations with the project redacted.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
DS="mf_day41_$(date +%s)"
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
  python aisle_schema.py --sql "$1" "$FULL" ${2:+"$2"} > "$SQL"
  for wait in 20 40 0; do
    if bq --quiet --project_id="$GCP_PROJECT_ID" --location=US --format=json query --nouse_legacy_sql --nouse_cache \
         --maximum_bytes_billed="$CAP" --max_rows=1000 < "$SQL" >"$out" 2>"$ERR"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|not ready|try again" "$out" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 41 $1 failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }
# needed_cap OUT: the "N or higher required" from a bytes-billed refusal, if it is within MAX_CAP
needed_cap() {
  local n; n=$(cat "$ERR" "$1" 2>/dev/null | grep -oE "[0-9]+ or higher required" | grep -oE "^[0-9]+" | head -1)
  [ -n "$n" ] && [ "$n" -le "$MAX_CAP" ] && echo "$n"
}
# run_graph OUT PART: try project.dataset.graph then dataset.graph; raise the bytes cap once if BigQuery asks
run_graph() {
  local out="$1" part="$2" ref
  for ref in "${FULL}.AisleGraph" "${DS}.AisleGraph"; do
    run_query "$out" "$part" "$ref" && { REF="$ref"; return 0; }
    if NEED=$(needed_cap "$out"); then
      echo "::notice title=Google Cloud day 41 billing::$part needs maximum_bytes_billed of at least $((NEED / 1048576)) MiB; re-running with that cap"
      CAP=$NEED; run_query "$out" "$part" "$ref" && { REF="$ref"; return 0; }
    fi
  done
  return 1
}

if ! run_query "$DIR/setup.json" setup; then
  echo "::warning title=Day 41 OPTIONS::graph DDL with OPTIONS(description) refused: $( (tail -c 400 "$ERR"; tail -c 300 "$DIR/setup.json") | redact); retrying without OPTIONS"
  run_query "$DIR/setup.json" setup-plain || fail "tables + CREATE PROPERTY GRAPH" "$DIR/setup.json"
fi

run_graph "$DIR/schema.json" schema || fail "CALL BQ.SHOW_GRAPH_EXPAND_SCHEMA" "$DIR/schema.json"
python aisle_schema.py --check-schema "$DIR/schema.json" || exit 1

run_graph "$DIR/rollup.json" rollup || fail "GRAPH_EXPAND + AGG rollup" "$DIR/rollup.json"
python aisle_schema.py --compare "$DIR/rollup.json" || exit 1
