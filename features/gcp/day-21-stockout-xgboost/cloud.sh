#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived US dataset, load 800 synthetic shelf-days (600 train, 200
# holdout), train a BigQuery ML BOOSTED_TREE_CLASSIFIER with xgboost_version = '2.1' (XGBOOST_VERSION option, GA
# 2026-08-27), predict the holdout, and check the model beats the naive "on_hand < forecast" rule by 10+ points and
# scores at least 85%. Also reports the XGBoost version BigQuery recorded in the model's training options.
# The dataset, with its table and model, is deleted on exit (the table also expires after an hour).
# Billing: a free load job; CREATE MODEL is billed at BigQuery ML's training minimum (the first run was refused under
# a 20 MiB cap: "532676608 or higher required", ~508 MiB), so it gets a 1 GiB cap, about $0.15 per run on on-demand
# pricing; ML.PREDICT at the 10 MiB minimum, capped at 20 MiB. Errors are annotations with the project ID redacted.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
STAMP=$(date +%s)
DS="mf_day21_${STAMP}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; ERR="$DIR/err.txt"
redact() { sed "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ' | tail -c 900; }
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap cleanup EXIT

if ! bq --quiet --location=US mk -d --default_table_expiration 3600 --label project:ai-frontier \
     "${GCP_PROJECT_ID}:${DS}" > "$ERR" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$ERR")"; exit 1
fi

python stockout.py --csv "$DIR/shelf_days.csv"
SCHEMA=$(python -c "import stockout; print(stockout.SCHEMA)")
if ! bq --quiet --location=US load --source_format=CSV --skip_leading_rows=1 \
     "${GCP_PROJECT_ID}:${DS}.shelf_days" "$DIR/shelf_days.csv" "$SCHEMA" > "$ERR" 2>&1; then
  echo "::error title=Day 21 load failed::$(redact < "$ERR")"; exit 1
fi

# run_query PART OUTFILE [MAX_BYTES]: SQL on stdin (a leading "--" comment would be read as a flag); retry transient
# errors. MAX_BYTES defaults to 20 MiB.
run_query() {
  python stockout.py --sql "$1" "${GCP_PROJECT_ID}.${DS}" > "$SQL"
  for wait in 20 40 0; do
    if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed="${3:-20971520}" \
         --max_rows=1000 < "$SQL" >"$2" 2>"$ERR"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|not ready|try again" "$2" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 21 $1 failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }

T0=$(date +%s)
run_query model "$DIR/model.json" 1073741824 || fail "CREATE MODEL (xgboost_version 2.1)" "$DIR/model.json"
echo "::notice title=Day 21 training::CREATE MODEL took $(( $(date +%s) - T0 ))s"
run_query predict "$DIR/predict.json" || fail "ML.PREDICT" "$DIR/predict.json"

# What BigQuery recorded: look for the xgboost version in the model's training options (informational only)
if bq --quiet --format=json show --model "${GCP_PROJECT_ID}:${DS}.stockout_xgb" > "$DIR/model_meta.json" 2>"$ERR"; then
  python - "$DIR/model_meta.json" <<'PY'
import json, sys
meta = json.load(open(sys.argv[1]))
opts = (meta.get("trainingRuns") or [{}])[-1].get("trainingOptions", {})
found = {k: v for k, v in opts.items() if "xgboost" in k.lower()}
print(f"::notice title=Day 21 model training options::{json.dumps(found) if found else 'no xgboost* key in trainingOptions'}; "
      f"modelType={meta.get('modelType')}")
PY
fi
python stockout.py --compare "$DIR/predict.json"
