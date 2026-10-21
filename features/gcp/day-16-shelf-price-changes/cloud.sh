#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived BigQuery dataset, create a shelf-price table with
# enable_change_history = TRUE, run a week of price edits as separate commits (one script, no transaction), then read
# every change back with the CHANGES table function (GA) and check it row by row against the local Python replay.
# The dataset is deleted on exit (tables also expire after an hour). A few KB of data: each DML statement bills the
# 10 MiB minimum (5 statements, cap 100 MiB); the CHANGES query reads one table (cap 20 MiB).
# Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
DS="mf_day16_$(date +%s)"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { sed "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ' | tail -c 900; }
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; rm -f "$SQL" "$OUT" "$ERR"; }
trap cleanup EXIT

if ! bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier \
     "${GCP_PROJECT_ID}:${DS}" > "$OUT" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$OUT")"; exit 1
fi

# run_query PART CAP: SQL on stdin (a leading "--" comment would be read as a flag); one retry on transient errors
run_query() {
  python price_changes.py --sql "$1" "${GCP_PROJECT_ID}.${DS}" > "$SQL"
  for wait in 15 0; do
    if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed="$2" \
         --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  echo "::error title=Day 16 $1 query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
  return 1
}

run_query setup 104857600 || exit 1
run_query changes 20971520 || exit 1
python price_changes.py --compare "$OUT"
