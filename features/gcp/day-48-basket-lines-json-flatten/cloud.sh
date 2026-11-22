#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery query flattens 8 grocery orders' nested line JSON with
# JSON_FLATTEN (GA 2026-11-11) and prices each basket; basket.py checks every order against its pure-Python
# reference. Orders are inlined in the SQL: 0 bytes scanned, nothing created, nothing to clean up.
# Errors are printed with the project ID and long numbers redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; OUT="$DIR/out.json"; ERR="$DIR/err.txt"
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c "${1:-900}"; }
trap 'rm -rf "$DIR"' EXIT

python basket.py --sql > "$SQL"
# SQL on stdin: a leading "--" comment would be read as a flag
for wait in 30 0; do
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then
    python basket.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|429" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=Day 48 JSON_FLATTEN query refused::$( (tail -c 400 "$ERR"; tail -c 400 "$OUT") | redact)"
exit 1
