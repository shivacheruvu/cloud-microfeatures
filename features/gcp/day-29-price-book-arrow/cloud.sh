#!/usr/bin/env bash
# Cloud run (GitHub Actions, keyless): the same inlined 120-row price book is queried three times over the BigQuery
# REST API (jobs.query): default JSON rows (timestamps as int64 micros), queryResultsFormat ARROW (Preview,
# 2026-10-30), and ARROW with ZSTD buffer compression. The Arrow batches are decoded with pyarrow and must equal the
# JSON rows and the local reference exactly, with the expected Arrow types. Nothing is created (0 bytes scanned).
# price_book.py redacts its own errors; everything it prints (tracebacks included) also goes through the same
# filter as the other days: project ID and long digit runs (project numbers) removed, because logs are public.
set -euo pipefail
: "${GCP_PROJECT_ID:?}"
redact() { sed -E -e "s/${GCP_PROJECT_ID}/<project>/g" -e 's/[0-9]{6,}/<number>/g'; }
python -m pip install -q "pyarrow>=18" >/dev/null 2>&1 || python -m pip install -q "pyarrow>=18"
python price_book.py --cloud 2>&1 | redact
