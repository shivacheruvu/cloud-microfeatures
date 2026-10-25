#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived US dataset, create a products node table, a substitutes edge
# table and a property graph over them (BigQuery Graph), then run one GQL query with a quantified path
# (-[:SubstitutesFor]->{1,3}) returning PATH_LENGTH, IS_ACYCLIC and IS_TRAIL (path functions GA 2026-08-31) for every
# walk from an out-of-stock product; check the acyclic walks and the substitute picked for each product equal the
# local model. If the quantified pattern is refused, the same walks are read with three fixed-length patterns
# (warning). The dataset (tables and graph) is deleted on exit; tables also expire after an hour.
# Billing: two tiny INSERTs and one or three graph queries, each at the 10 MiB minimum and capped at 20 MiB.
# Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
STAMP=$(date +%s)
DS="mf_day20_${STAMP}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; ERR="$DIR/err.txt"
redact() { sed "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ' | tail -c 900; }
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap cleanup EXIT

if ! bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier \
     "${GCP_PROJECT_ID}:${DS}" > "$ERR" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$ERR")"; exit 1
fi

# run_query PART OUTFILE: SQL on stdin (a leading "--" comment would be read as a flag); retry transient errors
run_query() {
  python substitutes.py --sql "$1" "${GCP_PROJECT_ID}.${DS}" > "$SQL"
  for wait in 20 40 0; do
    if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
         --max_rows=1000 < "$SQL" >"$2" 2>"$ERR"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|not ready|try again" "$2" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 20 $1 failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }

run_query setup "$DIR/setup.json" || fail "tables + CREATE PROPERTY GRAPH" "$DIR/setup.json"
if run_query walks "$DIR/walks.json"; then
  python substitutes.py --compare "$DIR/walks.json"
else
  echo "::warning title=Day 20 quantified path refused::$( (tail -c 400 "$ERR"; tail -c 400 "$DIR/walks.json") | redact) - using fixed-length patterns"
  for h in 1 2 3; do run_query "fixed$h" "$DIR/fixed$h.json" || fail "GQL fixed $h-hop query" "$DIR/fixed$h.json"; done
  python substitutes.py --compare "$DIR/fixed1.json" "$DIR/fixed2.json" "$DIR/fixed3.json"
fi
