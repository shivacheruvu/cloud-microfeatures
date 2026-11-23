#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): in a short-lived US dataset, create a 12-row store price table and three row
# access policies granted to the runner's own service account, then read them back from
# INFORMATION_SCHEMA.ROW_ACCESS_POLICIES (GA, 2026-11-18). audit.py replays every stored filter predicate in DuckDB
# and checks the protected table returns exactly the union of rows those predicates open.
# The dataset is deleted on exit (tables also expire after an hour). The service account email is substituted into
# the SQL only and never printed; errors are annotations with the project ID, emails and long numbers redacted.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
DS="mf_day49_$(date +%s)"
DIR=$(mktemp -d); SQL="$DIR/q.sql"; ERR="$DIR/err.txt"
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+/<account>/g' \
  -e 's/[0-9]{6,}/<number>/g' | tail -c "${1:-900}"; }
cleanup() { bq --quiet rm -r -f -d "${GCP_PROJECT_ID}:${DS}" >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap cleanup EXIT

python -c "import duckdb" 2>/dev/null || pip install -q duckdb >/dev/null 2>&1 || {
  echo "::error title=Day 49 needs duckdb::could not install duckdb for the predicate replay"; exit 1; }

RUNNER=$(gcloud config get-value account 2>/dev/null)
case "$RUNNER" in
  *.iam.gserviceaccount.com) ;;
  *) echo "::error title=Day 49::the runner is not signed in as a service account; row access policies need a grantee"; exit 1 ;;
esac

if ! bq --quiet --location=US mk -d --default_table_expiration 3600 "${GCP_PROJECT_ID}:${DS}" > "$ERR" 2>&1; then
  echo "::error title=Create dataset failed::$(redact < "$ERR")"; exit 1
fi

# run_query OUTFILE PART: SQL on stdin (a leading "--" comment would be read as a flag); retry transient errors
run_query() {
  local out="$1"
  python audit.py --sql "$2" "$DS" | sed "s/__RUNNER__/${RUNNER}/g" > "$SQL"
  for wait in 20 0; do
    if bq --quiet --project_id="$GCP_PROJECT_ID" --location=US --format=json \
         query --nouse_legacy_sql --nouse_cache --maximum_bytes_billed=20971520 --max_rows=100 < "$SQL" >"$out" 2>"$ERR"; then
      return 0
    fi
    if [ "$wait" = 0 ] || ! grep -qiE "internal error|transient|backendError|try again" "$out" "$ERR"; then break; fi
    echo "::warning title=BigQuery transient error::retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}
fail() { echo "::error title=Day 49 $1 failed::$( (tail -c 600 "$ERR"; tail -c 600 "$2") | redact)"; exit 1; }

run_query "$DIR/setup.json" setup || fail "CREATE TABLE store_prices" "$DIR/setup.json"
run_query "$DIR/pol.json" policies || fail "CREATE ROW ACCESS POLICY" "$DIR/pol.json"

# the view may lag a freshly created policy: poll until all three are listed (about 2 minutes at most)
FORM=view; GOT=0
for wait in 15 30 30 45 0; do
  if run_query "$DIR/view.json" "$FORM"; then
    GOT=$(python -c "import sys,json; sys.path.insert(0,'.'); import audit; r=audit.read_rows(sys.argv[1]); print(len(r or []))" "$DIR/view.json")
    [ "$GOT" -ge 3 ] && break
  elif [ "$FORM" = view ]; then
    echo "::notice title=Google Cloud day 49::dataset-qualified view refused, trying region-us: $( (tail -c 300 "$ERR") | redact 300)"
    FORM=view-region; continue
  else
    fail "INFORMATION_SCHEMA.ROW_ACCESS_POLICIES" "$DIR/view.json"
  fi
  [ "$wait" = 0 ] || sleep "$wait"
done
[ "$GOT" -lt 3 ] && echo "::warning title=Google Cloud day 49::the view listed $GOT of 3 policies after polling"

run_query "$DIR/rows.json" rows || fail "SELECT through the policies" "$DIR/rows.json"
python audit.py --compare "$DIR/view.json" "$DIR/rows.json"
