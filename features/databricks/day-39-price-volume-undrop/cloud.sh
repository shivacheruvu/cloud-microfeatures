#!/usr/bin/env bash
# Cloud run (GitHub Actions): no SQL warehouse needed. Uploads three store price files into a fresh managed Unity
# Catalog volume (comment + READ VOLUME grant to account users) over REST, then submits undrop_volume.py as a one-off
# serverless job (environment version 6) that snapshots the volume, DROPs it, finds it with SHOW VOLUMES DROPPED and
# restores it with UNDROP VOLUME WITH ID (2026-11-07). Both snapshots must equal the local reference (result.json) and
# the restored volume must keep its ID and comment. The workspace folder, volumes and schema are deleted on exit.
# The hostname and user name are never printed.
set -euo pipefail
python run_cloud.py
