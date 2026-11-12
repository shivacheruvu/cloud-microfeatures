#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery query counts the items in eight nested tote manifests with
# JSON_FLATTEN (GA, 2026-11-11) next to the old fixed-depth JSON_QUERY_ARRAY unnest; every number per order must equal
# the local model. A second query runs JSON_FLATTEN's documented examples plus a few undocumented edge cases (empty
# arrays, JSON null, SQL NULL) and reports them next to the local model (informational).
# Orders are inlined in the SQL: 0 bytes scanned, nothing is created, nothing needs cleaning up.
# Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 700; }
trap 'rm -f "$SQL" "$OUT" "$ERR"' EXIT
bqq() {  # SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
  bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 --max_rows=100 \
    < "$SQL" >"$OUT" 2>"$ERR"
}

# 1. edge cases (informational)
python tote_manifest.py --edge-sql > "$SQL"
if bqq; then
  python tote_manifest.py --edge-compare "$OUT"
else
  echo "::warning title=Day 38 edge-case query failed::$( (tail -c 400 "$ERR"; tail -c 400 "$OUT") | redact)"
fi

# 2. main query (required)
python tote_manifest.py --sql > "$SQL"
for wait in 10 30 0; do
  if bqq; then
    python tote_manifest.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=Day 38 JSON_FLATTEN query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
exit 1
