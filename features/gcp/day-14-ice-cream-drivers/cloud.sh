#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): correlate daily ice cream revenue with temperature, promotion depth and foot
# traffic for 3 stores x 28 days of inlined data with BigQuery ML.CORRELATION (Preview), PEARSON and SPEARMAN, and check
# every correlation against the local pure-Python one. No dataset is created, so nothing to clean up. A few KB of
# inlined data, capped at 20 MiB billed. Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
python drivers.py --sql > "$SQL"
for wait in 10 30 0; do
  # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then
    python drivers.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
MSG=$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g')
echo "::error title=ML.CORRELATION query failed::${MSG}"
exit 1
