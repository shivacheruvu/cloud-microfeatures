#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): run ML.SEASONALITY on BigQuery over the inlined daily data (no dataset, so
# nothing to clean up) and check its weekly component against the local decomposition. A few KB scanned: free tier.
# --max_rows: the output is one row per store and day (168), above bq's default of 100.
# Errors are reported as annotations with the project ID redacted, since run logs are public.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
python seasonality.py --sql > "$SQL"
for wait in 10 30 0; do  # retry only BigQuery's "transient internal error" failures
  # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 --max_rows=1000 < "$SQL" >"$OUT" 2>"$ERR"; then
    python seasonality.py --compare "$OUT"; exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
msg=$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | sed "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ')
echo "::error title=ML.SEASONALITY query failed::${msg}"
exit 1
