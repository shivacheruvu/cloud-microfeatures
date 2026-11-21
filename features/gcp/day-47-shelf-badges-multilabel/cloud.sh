#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): one BigQuery query tags 14 grocery products with diet badges (vegan,
# vegetarian, gluten_free, dairy_free, contains_nuts) using multi-label AI.CLASSIFY (output_mode => 'multi', GA
# 2026-11-17), next to the default single-label AI.CLASSIFY on the same input. Products are inlined in the SQL, so
# nothing is created and nothing needs cleaning up. The call form is probed (forms in badges.py): the first form
# BigQuery accepts is checked against the answer key; if none is accepted, every form's error is annotated.
# AI.CLASSIFY calls Gemini through the Vertex AI API (enabled since day 26; made sure here). About 28 short calls,
# under a cent. Errors are printed with the project ID and long numbers redacted (logs are public).
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; OUT="$DIR/out.json"; ERR="$DIR/err.txt"
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c "${1:-900}"; }
trap 'rm -rf "$DIR"' EXIT

if [ -z "$(gcloud services list --enabled --filter='config.name=aiplatform.googleapis.com' --format='value(config.name)' 2>/dev/null)" ]; then
  if gcloud services enable aiplatform.googleapis.com --quiet >/dev/null 2>"$ERR"; then
    echo "::notice title=Google Cloud day 47::enabled the Vertex AI API (aiplatform.googleapis.com) for AI.CLASSIFY"
  else
    echo "::error title=Could not enable the Vertex AI API::$(tail -c 600 "$ERR" | redact)"; exit 1
  fi
fi

# run_form N: SQL on stdin (a leading "--" comment would be read as a flag); retry transient errors
run_form() {
  python badges.py --sql "$1" > "$SQL"
  for wait in 30 60 0; do
    if bq --quiet --location=US --format=json query --nouse_legacy_sql --maximum_bytes_billed=20971520 \
         --max_rows=100 < "$SQL" >"$OUT" 2>"$ERR"; then
      return 0
    fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|resource exhausted|429" "$OUT" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}

TRIED=""
for n in 1 2 3; do
  if run_form "$n"; then
    [ -n "$TRIED" ] && echo "::notice title=Google Cloud day 47 call form::form $n accepted; earlier forms refused:$(echo "$TRIED" | tail -c 700)"
    python badges.py --compare "$OUT" "$n"
    exit $?
  fi
  TRIED="$TRIED [form $n] $( (tail -c 300 "$ERR"; tail -c 300 "$OUT") | redact 300)"
done
echo "::error title=Day 47 multi-label AI.CLASSIFY refused in every call form::$(echo "$TRIED" | tail -c 950)"
exit 1
