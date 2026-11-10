#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery query compresses five scan exports with ZSTD_COMPRESS at levels
# -5, 1, 3 (and the default) and 19, and reads them back with ZSTD_DECOMPRESS_TO_STRING / ZSTD_DECOMPRESS_TO_BYTES
# (GoogleSQL compression functions, Preview, 2026-11-09). Every frame BigQuery returns must decompress locally with
# libzstd to the exact export. A second query checks the size_limit guard refuses a 9.9 KB export at 4 KiB (reported,
# not required). Exports are inlined in the SQL: nothing is created, nothing needs cleaning up. Errors are printed
# with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
python -m pip install -q zstandard >/dev/null 2>&1 || { echo "::error title=Day 36::pip install zstandard failed"; exit 1; }
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 700; }
trap 'rm -f "$SQL" "$OUT" "$ERR"' EXIT
bqq() {  # SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
  bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 --max_rows=100 \
    < "$SQL" >"$OUT" 2>"$ERR"
}

# 1. guard (informational): a decompress over size_limit must fail.
python scan_archive.py --guard-sql > "$SQL"
if bqq; then
  echo "::warning title=Day 36 size_limit guard::ZSTD_DECOMPRESS_TO_BYTES returned a value over size_limit => 4096 ($(tr -d '\n ' < "$OUT" | tail -c 120))"
elif grep -qi "too large" "$OUT" "$ERR"; then
  echo "::notice title=Day 36 size_limit guard::refused as documented: $(grep -ohiE 'ZSTD output is too large[^"]{0,80}' "$OUT" "$ERR" | head -1 | redact)"
else
  echo "::warning title=Day 36 size_limit guard::failed differently: $( (tail -c 400 "$ERR"; tail -c 400 "$OUT") | redact)"
fi

# 2. main query (required)
python scan_archive.py --sql > "$SQL"
for wait in 10 30 0; do
  if bqq; then
    python scan_archive.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=Day 36 ZSTD_COMPRESS query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
exit 1
