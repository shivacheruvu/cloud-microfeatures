#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): load the CSV into a short-lived BigQuery dataset, run the scorecard,
# check it against the local result, then delete the dataset. Well inside the BigQuery free tier (a few KB).
# Each step reports its own error as an annotation (project ID redacted), since run logs are public.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
DS="mf_day01_$(date +%s)"
LOG=$(mktemp)
step() {  # step "<name>" cmd...
  local name=$1; shift
  if ! "$@" >"$LOG.out" 2>"$LOG"; then
    msg=$( (tail -c 600 "$LOG"; tail -c 600 "$LOG.out") | sed "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ')
    echo "::error title=${name} failed::${msg}"
    exit 1
  fi
}
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; }
trap cleanup EXIT
pip install -q duckdb
step "Create dataset" bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier "${GCP_PROJECT_ID}:${DS}"
step "Load CSV" bq --quiet --location=US load --source_format=CSV --skip_leading_rows=1 "${GCP_PROJECT_ID}:${DS}.products" data/products.csv \
  barcode:STRING,name:STRING,category:STRING,nutri_grade:STRING,health_score:INT64,organic:BOOL,high_risk_additives:INT64
python -c "import scorecard; print(scorecard.bigquery_sql('${GCP_PROJECT_ID}.${DS}.products'))" > /tmp/scorecard.sql
# SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
run_query() { bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=10000000 < /tmp/scorecard.sql; }
step "Run query" run_query
cp "$LOG.out" /tmp/bq.json
python scorecard.py --compare /tmp/bq.json
