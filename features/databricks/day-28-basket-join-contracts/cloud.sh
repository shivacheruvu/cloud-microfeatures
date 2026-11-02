#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a serverless SQL warehouse, create three scratch tables and three Unity Catalog metric
# views, declare basket_mv -> products_mv and basket_mv -> stores_mv as MANY TO ONE relationships (Beta, 2026-10-30),
# read them back with DESCRIBE TABLE EXTENDED, audit each one with metric view queries and check the result equals the
# local run. The schema is dropped on exit. If the workspace refuses compute (account inactive), that is a warning.
set -euo pipefail
python contracts.py --databricks
