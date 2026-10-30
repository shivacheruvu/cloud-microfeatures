#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery query counts the input tokens of 48 shelf-tag prompts (24 grocery
# products x 2 templates) with AI.COUNT_TOKENS (GA, 2026-10-27). No generation call is made. The prompts are inlined
# in the SQL, so nothing is created and nothing needs cleaning up. The counts are checked against the local estimate
# (see token_budget.py). Errors are printed with the project ID redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { sed -e "s/${GCP_PROJECT_ID}/<project>/g" | tr '\n' ' ' | tail -c 900; }
trap 'rm -f "$SQL" "$OUT" "$ERR"' EXIT
python token_budget.py --sql > "$SQL"
for wait in 10 30 0; do
  # SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then
    python token_budget.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=AI.COUNT_TOKENS query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
exit 1
