#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): run AI.PREDICT (TabFM, zero-shot) on BigQuery over the inlined items (no dataset or model, so
# nothing to clean up) and check its forecasts for 8 new items beat the naive average, as the local
# least-squares model does. A few KB scanned: free tier (Preview pricing: bytes processed).
# Output: one row per new item (8).
# Errors are reported as annotations with the project ID redacted, since run logs are public.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
python forecast.py --sql > "$SQL"
for wait in 10 30 0; do  # retry only BigQuery's "transient internal error" failures
  # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 --max_rows=1000 < "$SQL" >"$OUT" 2>"$ERR"; then
    python forecast.py --compare "$OUT"; exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
msg=$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | sed "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ')
echo "::error title=AI.PREDICT query failed::${msg}"
exit 1
