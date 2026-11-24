#!/usr/bin/env bash
# Cloud run (GitHub Actions): on the workspace's serverless SQL warehouse (Statement Execution API), one ai_decide call
# per row (Beta, Databricks platform release notes, October 2026) answers three questions for 12 out-of-stock
# substitutions: verdict (choice), new_allergen (noul), closeness (score). Each answer must be well formed; agreement
# with the store rule's answer key is reported. Read-only: the pairs are inlined, nothing is created. Hostname and
# user name are never printed. A refused workspace or an ai_decide preview that is switched off is a warning.
set -euo pipefail
python substitutions.py --databricks
