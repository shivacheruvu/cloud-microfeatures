# Daily build playbook (for Claude and any other agent)

This repo ships **two microfeatures every day for 80 days**: one on **Databricks** and one on **Google Cloud**,
each in its own track. Both land in this repo; the website shows the two tracks separately.
Every microfeature must be built on a **new** platform feature: something released in the last 180 days,
picked from the official release notes that morning.
**Completion beats ambition.** Shiva values a finished, working feature over an ambitious unfinished one. If the
newest thing is too big, ship a small finished slice of it.

## Before you start
1. Work out today's day for each track with `python scripts/manifest_tools.py next` (prints `databricks=N gcp=M`).
   A track above 80 is complete; skip it.
2. **Check the release notes first, every day, for both tracks.** Pick something released in the last 180 days that
   isn't already in `manifest.json`:
   - Databricks: https://docs.databricks.com/aws/en/release-notes/product/ (also `.../release-notes/serverless/`
     and `.../sql/release-notes/`). Days 1–14 are on the 14-day trial; from day 15, use only what
     **Databricks Free Edition** supports (serverless, no classic compute, no GPUs).
   - Google Cloud: https://cloud.google.com/release-notes (also https://cloud.google.com/bigquery/docs/release-notes).
     Once `bq` is authenticated, `bigquery-public-data.google_cloud_release_notes.release_notes` is queryable too.
   - Prefer GA or Public Preview features that run on serverless / free-tier resources and fit the grocery-data
     theme in `ROADMAP.md`. Skip anything that needs paid tiers, GPUs or sales contact.
3. Read the matching skill before touching a cloud service. Skills live in Shiva's Obsidian vault
   (`~/Documents/Obsidian Vault/50 Skills Library/packages/`) and upstream at
   `github.com/databricks/databricks-agent-skills` and `github.com/google/skills`. For gcloud, follow Google's rule:
   check `gcloud help <command>` before using any command.
4. Check `reports/` for yesterday's notes, open problems and anything Shiva asked for.

## What a finished day looks like (per track)
- `features/<databricks|gcp>/day-NN-short-name/` containing:
  - `README.md`: what it does, **which new platform feature it uses (with the release-notes link and date)**, why it
    matters, how to run it, what it costs, and the result (numbers or a table)
  - the code (SQL / Python / YAML bundle / Terraform)
  - a local test under `tests/` that runs in CI **without** cloud credentials (pyspark local, duckdb, or pure Python)
  - `result.json` with a few headline numbers the website can show (keep it small)
- A manifest entry with `platform`, `day`, `path`, and `new_feature: {name, released, source}`.
  `python scripts/manifest_tools.py validate` rejects features older than 180 days. Run
  `python scripts/manifest_tools.py render` to update the README tables.
- One `reports/YYYY-MM-DD.md` covering both tracks: what shipped, which release notes were checked, what ran in the
  cloud, cost touched, what's blocked, what's next.
- Merged through one pull request: open it with `gh api repos/shivacheruvu/cloud-microfeatures/pulls`
  (GraphQL is blocked in Claude sessions), wait for the `gitleaks` and `test` checks, then merge with
  `gh api -X PUT repos/shivacheruvu/cloud-microfeatures/pulls/N/merge -f merge_method=squash`.

Order of work each day: finish and merge one track before starting the other, so a long day still ships something.
If one track can't be finished, merge the finished one and record the other as `blocked` with the reason.

Status values: `built` (code + local test), `ran` (also ran in the cloud), `ran-local` (cloud not available yet, ran
locally), `blocked` (explain in the report).

## Cloud runs
- Cloud runs happen in GitHub Actions (`.github/workflows/daily.yml`), right after a merge that touches `features/`
  and every morning. A Databricks feature ships a `databricks.yml` bundle with a `daily` job; a Google Cloud feature
  ships a `cloud.sh` that uses `$GCP_PROJECT_ID` / `$GCP_REGION`, is safe to re-run, and cleans up what it doesn't need.
- After merging, watch that workflow run (`gh api repos/shivacheruvu/cloud-microfeatures/actions/runs?branch=main`)
  and, once the cloud part succeeds, mark the feature `ran` in a follow-up PR (or the next day's PR).
- GCP access is keyless: repository variables `GCP_WIF_PROVIDER` and `GCP_SERVICE_ACCOUNT`, set up once with
  `scripts/gcp_setup_keyless.sh`. Only workflows on `main` can sign in.
- Credentials exist only as GitHub Actions secrets (`DATABRICKS_HOST`, `DATABRICKS_TOKEN`, and GCP via Workload
  Identity Federation). Never print, log or commit them. Never ask for them in chat.
- If the secrets aren't set yet, build and test locally, mark the feature `ran-local`, and say in the report what
  Shiva needs to do.
- Smallest compute that works: serverless or single node, auto-termination on, delete what you create unless the
  feature is meant to keep running.
- Databricks: the trial ends after day 14. Never add a payment method or cloud account to "upgrade" it. From day 15,
  Free Edition only.
- GCP: stay well inside the free-trial credits; never upgrade the billing account. Note estimated cost in each
  feature README.

## Hard rules
- No API keys, tokens, account IDs, billing IDs or personal data in any file.
- Every push is secret-scanned; `main` only changes through a passing pull request.
- Don't rewrite or delete earlier days' features; fix forward with a note.
