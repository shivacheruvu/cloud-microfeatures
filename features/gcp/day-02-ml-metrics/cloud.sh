#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): load the CSV into a short-lived BigQuery dataset, score both predictors with
# ML.METRICS, check the metrics against the local result, then delete the dataset. A few KB: BigQuery free tier.
# Each step reports its own error as an annotation (project ID redacted), since run logs are public.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
DS="mf_day02_$(date +%s)"
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
step "Create dataset" bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier "${GCP_PROJECT_ID}:${DS}"
step "Load CSV" bq --quiet --location=US load --source_format=CSV --skip_leading_rows=1 "${GCP_PROJECT_ID}:${DS}.products" data/products.csv \
  barcode:STRING,name:STRING,category:STRING,nutri_grade:STRING,health_score:INT64,organic:BOOL,high_risk_additives:INT64
for q in classification regression; do
  python -c "import metrics; print(metrics.bigquery_sql('$q', '${GCP_PROJECT_ID}.${DS}.products'))" > "/tmp/$q.sql"
  # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
  step "ML.METRICS ($q)" bash -c "bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 < /tmp/$q.sql"
  cp "$LOG.out" "/tmp/$q.json"
done
python metrics.py --compare /tmp/classification.json /tmp/regression.json
