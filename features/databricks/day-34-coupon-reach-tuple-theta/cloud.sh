#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Submits coupon_reach.py as a one-off serverless job task on
# environment version 6: it builds per-store tuple sketches of loyalty spend and a theta sketch of e-mailed customers,
# combines them with tuple_intersection_theta_double / tuple_difference_theta_double / tuple_union_theta_double
# (Databricks SQL 2026.15) and checks everything equals the local reference (result.json). The workspace folder,
# scratch volume and schema are deleted on exit. The workspace hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
