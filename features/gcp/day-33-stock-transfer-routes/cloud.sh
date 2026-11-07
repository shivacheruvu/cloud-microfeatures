#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived US dataset, create a stores node table, a roads edge table and a
# property graph over them (BigQuery Graph), then run one GQL query on ON-DEMAND pricing (no reservation; allowed since
# the 2026-11-06 release note): MATCH ANY SHORTEST from each surplus store to each short store over 1-3 Road edges, with
# LET minutes = SUM(e.minutes). The rows and the transfer plan built from them must equal the local model.
# If the project still gets "require a reservation with Enterprise" (the change hasn't reached it yet), the script
# warns and stops without failing the workflow; no reservation or paid edition is ever bought.
# The dataset (tables and graph) is deleted on exit; tables also expire after an hour.
# Billing: two tiny INSERTs and one graph query, each at the 10 MiB minimum and capped at 20 MiB (well under a cent).
# Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
STAMP=$(date +%s)
DS="mf_day33_${STAMP}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; ERR="$DIR/err.txt"
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap cleanup EXIT

if ! bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier \
     "${GCP_PROJECT_ID}:${DS}" > "$ERR" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$ERR")"; exit 1
fi

# run_query PART OUTFILE: SQL on stdin (a leading "--" comment would be read as a flag); retry transient errors
run_query() {
  python transfer_routes.py --sql "$1" "${GCP_PROJECT_ID}.${DS}" > "$SQL"
  for wait in 20 40 0; do
    if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
         --max_rows=1000 < "$SQL" >"$2" 2>"$ERR"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|not ready|try again" "$2" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 33 $1 failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }

run_query setup "$DIR/setup.json" || fail "tables + CREATE PROPERTY GRAPH" "$DIR/setup.json"
if run_query routes "$DIR/routes.json"; then
  python transfer_routes.py --compare "$DIR/routes.json"
elif grep -qi "require a reservation" "$ERR" "$DIR/routes.json"; then
  echo "::warning title=Google Cloud day 33::the GQL query was still refused on on-demand pricing (needs an Enterprise reservation); the 2026-11-06 change may not have reached this project yet, so the routes are verified locally only"
else
  fail "GQL ANY SHORTEST query" "$DIR/routes.json"
fi
