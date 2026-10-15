#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): run AI.FORECAST with TimesFM 3.0 on BigQuery over 12 weeks of inlined daily
# strawberry sales, with the promo calendar as a future covariate, and check the 14-day forecast against the held-out
# sales (it must beat the seasonal naive forecast). A second, univariate query shows what the covariate adds; if
# only that one fails it's a warning. No dataset or model is created, so nothing to clean up. A few KB scanned,
# capped at 20 MiB billed. Errors are reported as annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"

run_query() {  # $1 = forecast.py mode, $2 = output file; retries only BigQuery's transient errors
  local sql err; sql=$(mktemp); err=$(mktemp)
  python forecast.py "$1" > "$sql"
  for wait in 10 30 0; do
    # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
    if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
         --max_rows=1000 < "$sql" >"$2" 2>"$err"; then return 0; fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$2" "$err"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  LAST_ERR=$( (tail -c 600 "$err"; tail -c 600 "$2") | sed "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ')
  return 1
}

OUT=$(mktemp); PLAIN=$(mktemp)
if ! run_query --sql "$OUT"; then
  echo "::error title=AI.FORECAST (TimesFM 3.0, promo covariate) query failed::${LAST_ERR}"
  exit 1
fi
if run_query --sql-plain "$PLAIN"; then
  python forecast.py --compare "$OUT" "$PLAIN"
else
  echo "::warning title=Univariate TimesFM 3.0 query failed::${LAST_ERR}"
  python forecast.py --compare "$OUT"
fi
