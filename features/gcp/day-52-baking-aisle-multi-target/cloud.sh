#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery AI.FORECAST call with TimesFM 3.0 multi-target mode
# (target_cols => ['flour', 'butter', 'eggs'], id_cols => ['store']) forecasts 14 days for 2 stores x 3 items from
# 16 weeks of inlined history; forecast.py checks all 84 values against the held-out sales. A second query runs the
# same six series as univariate TimesFM 2.5 forecasts for comparison (a failure there is only a warning).
# No dataset, table or model is created, so nothing to clean up. A few KB scanned, capped at 20 MiB billed.
# Errors are reported as annotations with the project ID and long numbers redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
DIR=$(mktemp -d); trap 'rm -rf "$DIR"' EXIT
LAST_ERR=""

run_query() {  # $1 = forecast.py mode, $2 = output file; retries only BigQuery's transient errors
  local sql="$DIR/q$RANDOM.sql" err="$DIR/err$RANDOM.txt"
  python forecast.py "$1" > "$sql"
  for wait in 10 30 0; do
    # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
    if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
         --max_rows=1000 < "$sql" >"$2" 2>"$err"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|429" "$2" "$err"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  LAST_ERR=$( (tail -c 600 "$err"; tail -c 600 "$2") | tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g')
  return 1
}

OUT="$DIR/multi.json"; UNI="$DIR/uni.json"
if ! run_query --sql "$OUT"; then
  echo "::error title=AI.FORECAST (TimesFM 3.0, target_cols) query failed::${LAST_ERR}"
  exit 1
fi
if run_query --sql-univariate "$UNI"; then
  python forecast.py --compare "$OUT" "$UNI"
else
  echo "::warning title=Univariate TimesFM 2.5 comparison query failed::${LAST_ERR}"
  python forecast.py --compare "$OUT"
fi
