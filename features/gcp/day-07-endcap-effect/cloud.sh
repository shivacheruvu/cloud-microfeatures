#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): run AI.CAUSAL_EFFECT on BigQuery over the inlined daily data (no dataset, so
# nothing to clean up) and check each product's effect class against the local weekday-mean
# counterfactual. A few KB scanned: free tier.
# Output: one summary row per product (3).
# Errors are reported as annotations with the project ID redacted, since run logs are public.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
python causal.py --sql > "$SQL"
for wait in 10 30 0; do  # retry only BigQuery's "transient internal error" failures
  # SQL goes in on stdin: as an argument, its leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 --max_rows=1000 < "$SQL" >"$OUT" 2>"$ERR"; then
    python causal.py --compare "$OUT"; exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
msg=$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g')
echo "::error title=AI.CAUSAL_EFFECT query failed::${msg}"
exit 1
