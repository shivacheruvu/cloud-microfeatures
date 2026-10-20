#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived BigQuery dataset, one multi-statement query creates 3 tables
# (scans, deliveries, shelf-tag prices, each with different columns) and a table function with an ANY TABLE parameter
# (GA 2026-08-11), calls it on all three, and the result is checked row by row against the local Python one. The
# dataset is deleted on exit (tables also expire after an hour). A few KB of inlined data, capped at 20 MiB billed.
# Errors are annotations with the project ID redacted (logs are public).
# (First version used temporary tables and CREATE TEMP TABLE FUNCTION; BigQuery doesn't support temporary table
# functions yet, so this was fixed forward on 2026-10-19.)
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
DS="mf_day15_$(date +%s)"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { sed "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ' | tail -c 900; }
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; rm -f "$SQL" "$OUT" "$ERR"; }
trap cleanup EXIT
if ! bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier \
     "${GCP_PROJECT_ID}:${DS}" > "$OUT" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$OUT")"; exit 1
fi
python freshness.py --sql "${GCP_PROJECT_ID}.${DS}" > "$SQL"
for wait in 10 30 0; do
  # SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then
    python freshness.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=ANY TABLE TVF query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
exit 1
