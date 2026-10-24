#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived US dataset, create the parse_pack Python UDF (BigQuery Python
# UDFs, GA 2026-05-20; body = parse_pack.py, standard library only), a 21-item table of shelf labels, and run
# unit_prices.sql, which calls the UDF; then check the rows equal the local model. The first call after CREATE
# FUNCTION waits for BigQuery to build the function's container (usually under a minute). The dataset, with its table
# and function, is deleted on exit (the table also expires after an hour). Billing: a CTAS and one query over 21 rows,
# each at the 10 MiB minimum and capped at 20 MiB, plus the Python UDF's few seconds of managed compute.
# Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
STAMP=$(date +%s)
DS="mf_day19_${STAMP}"
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
  python pack_prices.py --sql "$1" "${GCP_PROJECT_ID}.${DS}" > "$SQL"
  for wait in 20 40 0; do
    if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
         --max_rows=100 < "$SQL" >"$2" 2>"$ERR"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|not ready|try again" "$2" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 19 $1 failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }

run_query udf "$DIR/udf.json"     || fail "CREATE FUNCTION (Python UDF)" "$DIR/udf.json"
run_query setup "$DIR/setup.json" || fail "setup query" "$DIR/setup.json"
T0=$(date +%s)
run_query query "$DIR/query.json" || fail "query calling the Python UDF" "$DIR/query.json"
echo "::notice title=Day 19 Python UDF::first query (including the container build) took $(( $(date +%s) - T0 ))s"
python pack_prices.py --compare "$DIR/query.json"
