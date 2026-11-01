#!/usr/bin/env bash
# Cloud run (GitHub Actions): uploads the three supplier delivery notes (XML) to a scratch Unity Catalog volume through
# the Files API, reads them on a serverless SQL warehouse with read_files(format => 'xml', rowTag => 'line') against
# an explicit schema plus a rescued data column (native XML support, GA 2026-10-29), and checks every line, every
# rescued value and the per-supplier totals equal the local run. Volume and schema are deleted on exit. If the
# workspace refuses compute (account inactive), that is a warning and nothing is left behind.
set -euo pipefail
python asn_xml.py --databricks
