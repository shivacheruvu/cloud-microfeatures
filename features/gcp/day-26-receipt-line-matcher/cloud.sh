#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery query scores 256 (receipt line, catalog product) pairs with
# AI.SIMILARITY on the gemini-embedding-2 model (GA in BigQuery's embedding functions, 2026-10-05). The lines and
# products are inlined in the SQL, so nothing is created and nothing needs cleaning up. The scores are checked for
# completeness and range, and Gemini's matches are reported next to the local trigram baseline (see matcher.py).
# Errors are printed with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { sed -e "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ' | tail -c 900; }
trap 'rm -f "$SQL" "$OUT" "$ERR"' EXIT
python matcher.py --sql > "$SQL"
for wait in 10 30 0; do
  # SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=1000 < "$SQL" >"$OUT" 2>"$ERR"; then
    python matcher.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=AI.SIMILARITY query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
exit 1
