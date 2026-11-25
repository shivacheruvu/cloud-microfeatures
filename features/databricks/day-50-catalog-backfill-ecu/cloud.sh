#!/usr/bin/env bash
# Cloud run (GitHub Actions): on the workspace's serverless SQL warehouse (Statement Execution API), a 24-product
# catalog is loaded in two batches into two tables in a scratch schema: one with efficient column updates on
# ('delta.enableColumnUpdates-preview' = 'true', Beta, Databricks platform release notes 2026-11-13) and a standard
# control. Both get ALTER TABLE ADD COLUMNS (ingredient_count INT, nut_warning BOOLEAN) and one backfill UPDATE; the
# values must equal the local reference, also after rolling ECU back (property false + REORG TABLE APPLY (PURGE)).
# The UPDATE's file metrics from DESCRIBE HISTORY are reported for both tables. The schema is dropped on exit.
# Hostname and user name are never printed. A refused workspace or the Beta being off is a warning.
set -euo pipefail
python catalog_backfill.py --databricks
