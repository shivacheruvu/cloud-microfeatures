#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery query parses 18 till-receipt lines with REGEXP_EXTRACT_GROUPS
# (Preview, 2026-10-26), two calls per line (packed and weighed patterns), and the parse is checked against the
# local Python `re` run. The lines are inlined in the SQL, so nothing is created and nothing needs cleaning up.
# Errors are printed with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
trap 'rm -f "$SQL" "$OUT" "$ERR"' EXIT
python receipt_parser.py --sql > "$SQL"
for wait in 10 30 0; do
  # SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then
    python receipt_parser.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=REGEXP_EXTRACT_GROUPS query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
exit 1
