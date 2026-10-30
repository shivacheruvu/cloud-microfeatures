#!/usr/bin/env bash
# Cloud run (GitHub Actions): no compute needed (no SQL warehouse, no job). Creates an empty scratch catalog with the
# five grocery schemas through the Unity Catalog REST API, then reconciles the three Data Classification scope
# versions with the Data Classification API (GA 2026-10-14): create, duplicate create, no-op re-apply, two PATCHes
# with an update_mask, delete. Every step's outcome and the config read back must equal the local run. The config and
# the scratch catalog are deleted on exit; classification is never turned on for an existing catalog. The workspace
# hostname is never printed.
set -euo pipefail
python scan_scope.py --databricks
