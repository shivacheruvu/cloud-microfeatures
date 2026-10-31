#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery query scores 256 (receipt line, catalog product) pairs with
# AI.SIMILARITY on the gemini-embedding-2 model (GA in BigQuery's embedding functions, 2026-10-05). The lines and
# products are inlined in the SQL, so nothing is created and nothing needs cleaning up. The scores are checked for
# completeness and range, and Gemini's matches are reported next to the local trigram baseline (see matcher.py).
# AI.SIMILARITY calls the embedding model through the Vertex AI API (aiplatform.googleapis.com, shown as "Agent
# Platform API"), which the first run (2026-10-30) found disabled; the script enables it (enabling is free, calls are
# billed per use) and retries while the change propagates. Errors are printed with the project ID and any long
# number (project numbers) redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
SQL=$(mktemp); OUT=$(mktemp); ERR=$(mktemp)
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
trap 'rm -f "$SQL" "$OUT" "$ERR"' EXIT
python matcher.py --sql > "$SQL"
if [ -z "$(gcloud services list --enabled --filter='config.name=aiplatform.googleapis.com' --format='value(config.name)' 2>/dev/null)" ]; then
  if gcloud services enable aiplatform.googleapis.com --quiet >/dev/null 2>"$ERR"; then
    echo "::notice title=Google Cloud day 26::enabled the Vertex AI API (aiplatform.googleapis.com) for AI.SIMILARITY"
  else
    echo "::error title=Could not enable the Vertex AI API::$(tail -c 600 "$ERR" | redact)"; exit 1
  fi
fi
for wait in 30 60 90 0; do
  # SQL goes in on stdin: as an argument, a leading "--" comment would be read as a flag.
  if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
       --max_rows=1000 < "$SQL" >"$OUT" 2>"$ERR"; then
    python matcher.py --compare "$OUT"
    exit $?
  fi
  if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|API is not enabled|has not been used" "$OUT" "$ERR"; then break; fi
  echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
done
echo "::error title=AI.SIMILARITY query failed::$( (tail -c 600 "$ERR"; tail -c 600 "$OUT") | redact)"
exit 1
