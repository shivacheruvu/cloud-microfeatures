#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits threshold_ranks.py as a one-off serverless job
# (environment version 6) that writes a week of basket totals for 3 stores to a Delta table in a scratch schema,
# stores one KLL sketch per store and day with kll_sketch_agg_bigint, merges them with kll_merge_agg_bigint and reads
# the share of baskets at or under $35 / $50 / $75 with kll_sketch_get_rank_bigint (Databricks Runtime 19, "KLL
# quantile functions in SQL", 2026-06-15). Every rank must be within 0.03 of the exact share. The workspace folder,
# volume and schema are deleted on exit. Hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
