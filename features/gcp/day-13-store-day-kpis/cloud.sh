#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): compute store-day KPIs for 3 stores x 14 days of inlined basket lines with
# BigQuery multi-level aggregation (Preview), e.g. AVG(SUM(revenue) GROUP BY sale_date), and check every KPI against
# the local DuckDB nested GROUP BYs. No dataset is created, so nothing to clean up. About 50 KB of inlined data,
# capped at 20 MiB billed. Errors are annotations with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
pip install -q duckdb
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
python kpis.py --sql > "$SQL"
for wait in 10 30 0; do
  # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then
    python kpis.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
MSG=$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | sed "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ')
echo "::error title=Multi-level aggregation query failed::${MSG}"
exit 1
