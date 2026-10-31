#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): create one drop bucket per store with `gcloud storage buckets create
# --labels-file` (Cloud SDK, 2026-10-27) plus a lifecycle file, so each bucket carries its owner, cost-center and
# retention labels from the moment it exists (no follow-up `buckets update`). Each bucket is read back with
# `buckets describe` and checked against the local plan; all buckets are deleted on exit, and leftovers from an
# interrupted earlier run are deleted first. Errors are printed with the project ID and bucket names redacted.
set -uo pipefail
: "${GCP_PROJECT_ID:?}"
RUN="${GITHUB_RUN_ID:-local}"
WORK=$(mktemp -d); OUT=$(mktemp); CREATED=()
redact() { tr -d '\n' | sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e "s/cmf-d24-[a-z0-9-]+/<bucket>/g" -e 's/[0-9]{6,}/<number>/g' | tail -c 900; }
cleanup() {
  for b in "${CREATED[@]}"; do gcloud storage rm -r "gs://${b}" --quiet >/dev/null 2>&1 || true; done
  rm -rf "$WORK" "$OUT" "${SDK:-}"
}
trap cleanup EXIT

# Google's rule: check the command's help before using it. The flag is new (2026-10-27), and the runner's
# preinstalled SDK can lag behind, so fetch the current SDK release into a temp folder when the flag is missing.
# It reuses the signed-in configuration and credentials from the auth step.
# (help text is captured first: with pipefail, `gcloud help | grep -q` reports a miss when grep stops reading early)
has_flag() { local h; h=$(gcloud help storage buckets create 2>/dev/null) || true; [[ "$h" == *--labels-file* ]]; }
if ! has_flag; then
  old_sdk=$(gcloud version 2>/dev/null | head -1)
  SDK=$(mktemp -d)
  if curl -fsSL https://dl.google.com/dl/cloudsdk/channels/rapid/google-cloud-sdk.tar.gz | tar -xz -C "$SDK" \
     && [ -x "$SDK/google-cloud-sdk/bin/gcloud" ]; then
    export PATH="$SDK/google-cloud-sdk/bin:$PATH"
    gcloud config set core/disable_usage_reporting true --quiet >/dev/null 2>&1 || true
    echo "::notice title=Day 24 Cloud SDK::runner had ${old_sdk}; using $(gcloud version 2>/dev/null | head -1) for this run"
  fi
fi
if ! has_flag; then
  echo "::error title=gcloud too old::this Cloud SDK ($(gcloud version 2>/dev/null | head -1)) has no --labels-file on buckets create yet"
  exit 1
fi

# leftovers from an interrupted run (buckets are empty; names start with cmf-d24-)
for old in $(gcloud storage ls 'gs://cmf-d24-*' 2>/dev/null | grep -oE 'gs://cmf-d24-[a-z0-9-]+'); do
  gcloud storage rm -r "$old" --quiet >/dev/null 2>&1 && echo "::notice title=Day 24 cleanup::deleted one leftover bucket"
done

while IFS=$'\t' read -r id labels lifecycle; do
  b="cmf-d24-${RUN}-${id}-${RANDOM}"
  if ! gcloud storage buckets create "gs://${b}" --location=US --uniform-bucket-level-access \
       --public-access-prevention --soft-delete-duration=0 --labels-file="$labels" \
       --lifecycle-file="$lifecycle" --quiet > "$OUT" 2>&1; then
    echo "::error title=Create labelled bucket failed (${id})::$(redact < "$OUT")"; exit 1
  fi
  CREATED+=("$b")
  if ! gcloud storage buckets describe "gs://${b}" --format=json > "$WORK/${id}.describe.json" 2> "$OUT"; then
    echo "::error title=Describe bucket failed (${id})::$(redact < "$OUT")"; exit 1
  fi
done < <(python drop_buckets.py --plan "$WORK" "$RUN")

python drop_buckets.py --compare "$WORK"
