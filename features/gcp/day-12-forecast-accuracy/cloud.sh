#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): score 14-day forecasts for three grocery items with AI.EVALUATE on BigQuery,
# TimesFM 3.0 next to TimesFM 2.5, over 18 weeks of inlined daily sales (16 weeks history, 2 weeks held out), and
# check the metrics against the local seasonal-naive baseline. If the query is rejected, it runs once with
# TimesFM 2.5 only for diagnosis (the run still fails). No dataset or model is created, so nothing to clean up.
# A few KB scanned, capped at 20 MiB billed. Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"

run_query() {  # $1 = models (comma-separated), $2 = output file; retries only BigQuery's transient errors
  local sql err; sql=$(mktemp); err=$(mktemp)
  python evaluate.py --sql "$1" > "$sql"
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

OUT=$(mktemp)
if run_query "TimesFM 3.0,TimesFM 2.5" "$OUT"; then
  python evaluate.py --compare "$OUT"
  exit $?
fi
echo "::error title=AI.EVALUATE (TimesFM 3.0 + 2.5) query failed::${LAST_ERR}"
if run_query "TimesFM 2.5" "$OUT"; then
  python evaluate.py --compare "$OUT" | sed 's/^::notice title=Google Cloud day 12::/::warning title=Diagnosis only (TimesFM 2.5)::/'
else
  echo "::warning title=TimesFM 2.5 diagnosis query failed too::${LAST_ERR}"
fi
exit 1
