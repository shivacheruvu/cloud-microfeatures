#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one multi-statement BigQuery query creates 3 temporary tables (scans, deliveries,
# shelf-tag prices, each with different columns) and a temporary table function with an ANY TABLE parameter (GA
# 2026-08-11), calls it on all three, and checks every (feed, store) row against the local Python result. Temporary
# objects end with the query, so nothing to clean up. A few KB of inlined data, capped at 20 MiB billed. Errors are
# annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
python freshness.py --sql > "$SQL"
for wait in 10 30 0; do
  # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then
    python freshness.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
MSG=$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | sed "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ')
echo "::error title=ANY TABLE TVF query failed::${MSG}"
exit 1
