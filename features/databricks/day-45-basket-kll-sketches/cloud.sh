#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits basket_sketches.py as a one-off serverless job
# (environment version 6) that writes a week of basket totals for 3 stores to a Delta table in a scratch schema,
# stores one KLL quantile sketch per store and day with kll_sketch_agg_bigint (Databricks Runtime 19, "KLL quantile
# functions in SQL", 2026-06-15), and merges the stored sketches into weekly per-store and chain-wide percentiles
# with kll_merge_agg_bigint / kll_sketch_get_quantile_bigint. Every sketch must count exactly its baskets and every
# percentile must be within 0.03 normalized rank of the exact one. The workspace folder, volume and schema are
# deleted on exit. Hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
