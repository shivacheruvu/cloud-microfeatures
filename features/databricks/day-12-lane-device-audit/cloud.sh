#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse, audit which store network each checkout
# device is on with try_ip_host, ip_version and ip_cidr_contains (Databricks SQL 2026.20), and check every device's
# canonical IP, version, matched store and status against the local Python ipaddress audit. Read-only.
set -euo pipefail
python audit.py --databricks
