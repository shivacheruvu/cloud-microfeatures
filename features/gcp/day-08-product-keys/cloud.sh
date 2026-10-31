#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived BigQuery dataset, create a product table with an identity
# column, load it, add the vendor feed's new items, try (and expect to fail) a hand-written key, read the keys,
# INFORMATION_SCHEMA and the scan facts, check them against local, then delete the dataset. A few KB: free tier.
# Each step reports its own error as an annotation (project ID redacted), since run logs are public.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
DS="mf_day08_$(date +%s)"
FQ="${GCP_PROJECT_ID}.${DS}"
W=$(mktemp -d)
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 700; }
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; rm -rf "$W"; }
trap cleanup EXIT
# SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
q() {  # q <statement name>  -> $W/<name>.json, returns bq's exit code
  python keys.py --sql "$1" "$FQ" > "$W/$1.sql"
  bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
     < "$W/$1.sql" > "$W/$1.json" 2> "$W/$1.err"
}
step() {  # step <statement name>: must succeed
  if ! q "$1"; then
    echo "::error title=BigQuery ${1} failed::$( (cat "$W/$1.err"; cat "$W/$1.json") | redact)"
    exit 1
  fi
}
pip install -q duckdb
if ! bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier \
     "${GCP_PROJECT_ID}:${DS}" > "$W/mk.out" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$W/mk.out")"; exit 1
fi
step create
step load
step add_new
if q explicit_key; then rejected=0; else rejected=1; fi
step products
if ! q identity_info; then
  echo "::warning title=INFORMATION_SCHEMA identity query failed::$( (cat "$W/identity_info.err"; cat "$W/identity_info.json") | redact)"
  echo "[]" > "$W/identity_info.json"
fi
step scan_facts
python keys.py --compare "$W/products.json" "$W/identity_info.json" "$W/scan_facts.json" "$rejected"
