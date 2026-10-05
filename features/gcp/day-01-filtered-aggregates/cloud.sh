#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): load the CSV into a short-lived BigQuery dataset, run the scorecard,
# check it against the local result, then delete the dataset. Well inside the BigQuery free tier (a few KB).
set -euo pipefail
: "${GCP_PROJECT_ID:?}"
DS="mf_day01_$(date +%s)"
pip install -q duckdb
cleanup() { bq rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; }
trap cleanup EXIT
bq --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier "${GCP_PROJECT_ID}:${DS}" >/dev/null
bq load --quiet --source_format=CSV --skip_leading_rows=1 "${GCP_PROJECT_ID}:${DS}.products" data/products.csv \
  barcode:STRING,name:STRING,category:STRING,nutri_grade:STRING,health_score:INT64,organic:BOOL,high_risk_additives:INT64
python -c "import scorecard; print(scorecard.bigquery_sql('${GCP_PROJECT_ID}.${DS}.products'))" > /tmp/scorecard.sql
bq query --quiet --nouse_legacy_sql --format=json --maximum_bytes_billed=10000000 < /tmp/scorecard.sql > /tmp/bq.json
python scorecard.py --compare /tmp/bq.json
