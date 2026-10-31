#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): build a 25-item weekly-ad table in a short-lived US dataset, run the deal
# finder written with reusable column aliases (GoogleSQL, GA 2026-10-15) and the same query with every expression
# repeated, and check both against the local DuckDB run. Reusable column aliases are opt-in: if the alias query is
# refused with "Unrecognized name", this run turns `region-us.reusable_column_aliases_mode` on for the project,
# retries until the setting takes effect (up to 10 minutes), and sets it back to the default (NULL, off) on exit.
# The dataset is deleted on exit (the table also expires after an hour). Billing: the CTAS and two queries over a
# 25-row table, each at the 10 MiB minimum and capped at 20 MiB; the ALTER PROJECT statements have no query charge.
# Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
STAMP=$(date +%s)
DS="mf_day18_${STAMP}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; ERR="$DIR/err.txt"
TURNED_ON=0
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }

set_aliases_mode() {   # $1: "on" or NULL
  printf 'ALTER PROJECT `%s`\nSET OPTIONS (`region-us.reusable_column_aliases_mode` = %s)\n' "$GCP_PROJECT_ID" "$1" > "$DIR/alter.sql"
  bq --quiet --location=US query --nouse_legacy_sql < "$DIR/alter.sql" > "$DIR/alter.out" 2>&1
}
cleanup() {
  if [ "$TURNED_ON" = 1 ]; then
    set_aliases_mode NULL || echo "::warning title=Day 18 restore failed::reusable_column_aliases_mode was left on: $(redact < "$DIR/alter.out")"
  fi
  bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true
  rm -rf "$DIR"
}
trap cleanup EXIT

if ! bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier \
     "${GCP_PROJECT_ID}:${DS}" > "$ERR" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$ERR")"; exit 1
fi

# run_query PART OUTFILE: SQL on stdin (a leading "--" comment would be read as a flag); quiet on failure
run_query() {
  python shelf_tags.py --sql "$1" "${GCP_PROJECT_ID}.${DS}" > "$SQL"
  for wait in 15 0; do
    if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
         --max_rows=100 < "$SQL" >"$2" 2>"$ERR"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$2" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 18 $1 query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }

run_query setup "$DIR/setup.json" || fail setup "$DIR/setup.json"

# the new part: the alias query (turn the opt-in setting on for this run if the project doesn't have it)
if ! run_query aliases "$DIR/aliases.json"; then
  if ! grep -qi "Unrecognized name" "$ERR" "$DIR/aliases.json"; then fail aliases "$DIR/aliases.json"; fi
  echo "::notice title=Day 18 opt-in::reusable column aliases are off in this project; turning them on in region-us for this run"
  if ! set_aliases_mode '"on"'; then
    echo "::error title=Day 18 could not enable reusable column aliases::$(redact < "$DIR/alter.out")"; exit 1
  fi
  TURNED_ON=1
  ok=0
  for i in $(seq 1 20); do
    sleep 30
    if run_query aliases "$DIR/aliases.json"; then ok=1; echo "::notice title=Day 18 opt-in::setting took effect after about $((i * 30))s"; break; fi
    grep -qi "Unrecognized name" "$ERR" "$DIR/aliases.json" || fail aliases "$DIR/aliases.json"
  done
  [ "$ok" = 1 ] || fail "aliases (setting not in effect after 10 minutes)" "$DIR/aliases.json"
fi

run_query expanded "$DIR/expanded.json" || fail expanded "$DIR/expanded.json"
python shelf_tags.py --compare "$DIR"
