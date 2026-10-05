#!/usr/bin/env bash
# One-time, keyless GitHub Actions -> Google Cloud setup (Workload Identity Federation).
# Run it in Cloud Shell (already signed in) with the project selected:  bash scripts/gcp_setup_keyless.sh
# It creates no keys. Only workflows on the `main` branch of this repo can sign in.
# Safe to re-run: existing resources are left as they are.
set -euo pipefail

REPO="shivacheruvu/cloud-microfeatures"
POOL="github"
PROVIDER="cloud-microfeatures"
SA_NAME="microfeatures-ci"

PROJECT_ID="$(gcloud config get-value project 2>/dev/null)"
[ -n "$PROJECT_ID" ] || { echo "Select a project first: gcloud config set project <id>"; exit 1; }
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "== Enabling the APIs the setup and early features need"
gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudresourcemanager.googleapis.com serviceusage.googleapis.com bigquery.googleapis.com storage.googleapis.com

echo "== Service account for CI"
gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name="cloud-microfeatures CI (keyless)"

echo "== Workload identity pool + GitHub provider (main branch of $REPO only)"
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers describe "$PROVIDER" --location=global --workload-identity-pool="$POOL" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location=global --workload-identity-pool="$POOL" \
    --display-name="cloud-microfeatures main" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository=='${REPO}' && assertion.ref=='refs/heads/main'"

echo "== Let that repo act as the service account"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" \
  --condition=None >/dev/null

echo "== Project role for the service account (sandbox project; budget alerts at \$25)"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${SA_EMAIL}" --role=roles/editor --condition=None >/dev/null

echo
echo "Done. Add these two GitHub repository VARIABLES (Settings > Secrets and variables > Actions > Variables)."
echo "They are identifiers, not secrets: they can't be used outside this repo's main branch."
echo "  GCP_WIF_PROVIDER = projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "  GCP_SERVICE_ACCOUNT = ${SA_EMAIL}"
