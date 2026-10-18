#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse, roll per-product allergen arrays up to each
# basket and aisle with collect_union (Databricks SQL 2026.35), and check every group's allergen set against the local
# Python set unions and the long-hand array_distinct(flatten(collect_list(...))) in the same query. Read-only.
set -euo pipefail
python allergens.py --databricks
