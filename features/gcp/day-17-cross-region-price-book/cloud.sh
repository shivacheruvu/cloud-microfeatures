#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): build a 36-SKU price book in a short-lived US dataset, copy it into a
# short-lived EU dataset with one cross-region `bq cp` (GA 2026-10-15), then mark one SKU down in the US and check:
# the EU copy kept its clustering and description, its counts / sums match local, its row fingerprint equals the US
# table's at copy time, and it did NOT change with the US markdown (a copy is a snapshot, not replication).
# Both datasets are deleted on exit (tables also expire after an hour). Billing: the CTAS and the UPDATE bill the
# 10 MiB minimum each, four check queries are capped at 20 MiB each; the copy job itself has no query charge (a few
# KB of cross-region transfer). Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
STAMP=$(date +%s)
US="mf_day17_us_${STAMP}"; EU="mf_day17_eu_${STAMP}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; ERR="$DIR/err.txt"
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
cleanup() {
  bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${US}" >/dev/null 2>&1 || true
  bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${EU}" >/dev/null 2>&1 || true
  rm -rf "$DIR"
}
trap cleanup EXIT

for pair in "US:$US" "EU:$EU"; do
  loc=${pair%%:*}; ds=${pair#*:}
  if ! bq --quiet --location="$loc" mk -d --default_table_expiration 3600 --label project:ai-frontier \
       "${GCP_PROJECT_ID}:${ds}" > "$ERR" 2>&1; then
    echo "::error title=Create $loc dataset failed::$(redact < "$ERR")"; exit 1
  fi
done

# run_query LOCATION OUTFILE CAP PART TARGET: SQL on stdin (a leading "--" comment would be read as a flag)
run_query() {
  python price_book.py --sql "$4" "$5" > "$SQL"
  for wait in 15 0; do
    if bq --quiet --location="$1" --format=json query --nouse_legacy_sql --maximum_bytes_billed="$3" \
         --max_rows=100 < "$SQL" >"$2" 2>"$ERR"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$2" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  echo "::error title=Day 17 $4 query failed ($1)::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"
  return 1
}

run_query US "$DIR/setup.json" 20971520 setup "${GCP_PROJECT_ID}.${US}" || exit 1

# the new part: one copy job from a US dataset to an EU dataset
copied=0
for flags in "" "--location=US"; do
  if bq --quiet $flags cp -f "${GCP_PROJECT_ID}:${US}.price_book" "${GCP_PROJECT_ID}:${EU}.price_book" > "$ERR" 2>&1; then
    copied=1; break
  fi
done
if [ "$copied" = 0 ]; then
  echo "::error title=Cross-region bq cp failed::$(redact < "$ERR")"; exit 1
fi
if ! bq --quiet --format=json show "${GCP_PROJECT_ID}:${EU}.price_book" > "$DIR/eu_meta.json" 2>"$ERR"; then
  echo "::error title=bq show of the EU copy failed::$(redact < "$ERR")"; exit 1
fi

run_query US "$DIR/us_at_copy.json" 20971520 check "${GCP_PROJECT_ID}.${US}.price_book" || exit 1
run_query EU "$DIR/eu.json" 20971520 check "${GCP_PROJECT_ID}.${EU}.price_book" || exit 1
run_query US "$DIR/edit.json" 20971520 edit "${GCP_PROJECT_ID}.${US}" || exit 1
run_query US "$DIR/us_after.json" 20971520 check "${GCP_PROJECT_ID}.${US}.price_book" || exit 1
run_query EU "$DIR/eu_after.json" 20971520 check "${GCP_PROJECT_ID}.${EU}.price_book" || exit 1
python price_book.py --compare "$DIR"
