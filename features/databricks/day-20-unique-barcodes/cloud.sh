#!/usr/bin/env bash
# Cloud run (GitHub Actions): on a Databricks serverless SQL warehouse (Free Edition compatible), create a product
# catalog with PRIMARY KEY (product_id) and an informational UNIQUE (upc) constraint (Public Preview), plus a scans table
# whose FOREIGN KEY references that UNIQUE barcode; load a catalog with two duplicate barcodes (accepted: the constraint
# isn't enforced), run the audit, check it equals the local DuckDB run and that information_schema records the
# constraints with the FK pointing at products_upc_uq. The scratch schema and tables are dropped on exit.
set -euo pipefail
python barcodes.py --databricks
