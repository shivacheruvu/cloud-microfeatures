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
# BigQuery asks callers to retry "internal error ... transient" job failures with back-off (SLA); the first cloud
# run of this feature hit one. Retry those (only those) up to 3 times.
query_with_retry() {  # query_with_retry <sql file>; prints the JSON of the first successful attempt
  local wait out=/tmp/attempt.out err=/tmp/attempt.err
  for wait in 10 30 60 0; do
    if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 < "$1" >"$out" 2>"$err"; then
      cat "$out"; return 0
    fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$out" "$err"; then
      cat "$out"; cat "$err" >&2; return 1
    fi
    echo "$1" >> /tmp/retries.txt
    sleep "$wait"
  done
}
for q in classification regression; do
  python -c "import metrics; print(metrics.bigquery_sql('$q', '${GCP_PROJECT_ID}.${DS}.products'))" > "/tmp/$q.sql"
  # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
  step "ML.METRICS ($q)" query_with_retry "/tmp/$q.sql"
  cp "$LOG.out" "/tmp/$q.json"
done
[ -s /tmp/retries.txt ] && echo "::warning title=BigQuery transient errors::retried $(wc -l < /tmp/retries.txt) time(s) before success"
python metrics.py --compare /tmp/classification.json /tmp/regression.json
