#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): run AI.KEY_DRIVERS on BigQuery over the inlined weekly data (no dataset, so
# nothing to clean up) and check every segment against the local result. A few KB scanned: BigQuery free tier.
# Errors are reported as annotations with the project ID redacted, since run logs are public.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
pip install -q duckdb
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
python key_drivers.py --sql > "$SQL"
for wait in 10 30 0; do  # retry only BigQuery's "transient internal error" failures
  # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 < "$SQL" >"$OUT" 2>"$ERR"; then
    python key_drivers.py --compare "$OUT"; exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
msg=$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | sed "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ')
echo "::error title=AI.KEY_DRIVERS query failed::${msg}"
exit 1
