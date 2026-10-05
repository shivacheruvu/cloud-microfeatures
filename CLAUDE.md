# Daily build playbook (for Claude and any other agent)

This repo ships **one microfeature per day for 80 days**. Days 1–14 are Databricks, days 15–80 are Google Cloud.
Shiva values a finished, working feature over an ambitious unfinished one.

## Before you start
1. Work out today's day number: the next day after the highest `day` in `manifest.json`. If it is above 80, stop:
   the program is complete.
2. Read `ROADMAP.md` for what that day should build, and the matching skill before touching a cloud service.
   Skills live in Shiva's Obsidian vault (`~/Documents/Obsidian Vault/50 Skills Library/packages/`) and upstream at
   `github.com/databricks/databricks-agent-skills` and `github.com/google/skills`. For gcloud, follow Google's rule:
   check `gcloud help <command>` before using any command.
3. Check `reports/` for yesterday's notes, open problems and anything Shiva asked for.

## What a finished day looks like
- `features/day-NN-short-name/` containing:
  - `README.md`: what it does, why it matters, how to run it, what it costs, and the result (numbers or a table)
  - the code (SQL / Python / YAML bundle / Terraform)
  - a local test under `tests/` that runs in CI **without** cloud credentials (pyspark local, duckdb, or pure Python)
  - `result.json` with a few headline numbers the website can show (keep it small)
- A new entry appended to `manifest.json` (`python scripts/manifest_tools.py render` then updates the README table)
- `reports/YYYY-MM-DD.md`: what shipped, what ran in the cloud, cost touched, what's blocked, what's next
- Merged through a pull request: open the PR with `gh api repos/shivacheruvu/cloud-microfeatures/pulls`
  (GraphQL is blocked in Claude sessions), wait for the `gitleaks` and `test` checks, then merge with
  `gh api -X PUT repos/shivacheruvu/cloud-microfeatures/pulls/N/merge -f merge_method=squash`.

Status values: `built` (code + local test), `ran` (also ran in the cloud), `ran-local` (cloud not available yet, ran
locally), `blocked` (explain in the report).

## Cloud runs
- Credentials exist only as GitHub Actions secrets (`DATABRICKS_HOST`, `DATABRICKS_TOKEN`, and GCP via Workload
  Identity Federation). Never print, log or commit them. Never ask for them in chat.
- If the secrets aren't set yet, build and test locally, mark the feature `ran-local`, and say in the report what
  Shiva needs to do.
- Smallest compute that works: serverless or single node, auto-termination on, delete what you create unless the
  feature is meant to keep running.
- Databricks trial: after day 14, no Databricks resource may keep running.
- GCP: stay well inside the free-trial credits. Note estimated cost in each feature README.

## Hard rules
- No API keys, tokens, account IDs, billing IDs or personal data in any file.
- Every push is secret-scanned; `main` only changes through a passing pull request.
- Don't rewrite or delete earlier days' features; fix forward with a note.
