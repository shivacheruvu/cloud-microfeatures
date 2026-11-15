# Day 41 · Google Cloud · Self-describing aisle graph: the GRAPH_EXPAND schema, read back with BQ.SHOW_GRAPH_EXPAND_SCHEMA

**New platform feature:** **`BQ.SHOW_GRAPH_EXPAND_SCHEMA`** system procedure and **`OPTIONS(description=...)` on graph
properties and measures**, part of the BigQuery Graph measures GA release:
`CALL BQ.SHOW_GRAPH_EXPAND_SCHEMA('dataset.graph', var)` fills `var` with a JSON description of the table
`GRAPH_EXPAND` returns: every column's name, type and mode, `"is_measure": true` for measures, and property options
such as descriptions. Announced in the [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)
on **2026-11-10** ([Work with measures](https://docs.cloud.google.com/bigquery/docs/graph-measures),
[System procedures](https://docs.cloud.google.com/bigquery/docs/reference/system-procedures)). Day 37 used the
measures themselves; this day uses the schema procedure and the property descriptions.

## What it does
A small shelf graph ([`aisle_schema.py`](aisle_schema.py), synthetic): 3 aisles, 7 products with planogram facings, and
12 **restock scans** (`ShelfScans → Products → Aisles`, scans are the single root). One Python definition produces
both the `CREATE PROPERTY GRAPH` DDL (7 properties / measures with `OPTIONS(description=...)`, 3
`MEASURE(...)`s) and the **schema contract**: the 12 `<NodeTable>_<property>` columns `GRAPH_EXPAND` should return.

In the cloud, `cloud.sh`:
1. builds the graph and runs `CALL BQ.SHOW_GRAPH_EXPAND_SCHEMA`; every contract column must be reported, exactly
   `Products_total_facings`, `ShelfScans_total_units` and `ShelfScans_scan_count` flagged as measures, and any
   description that comes back must match the one in the DDL (extra columns and missing descriptions are reported);
2. runs a `GRAPH_EXPAND` + `AGG` rollup per aisle over the promised columns; every number must equal the local model
   (DuckDB and plain Python, which the test requires to agree).

## Why it matters
`GRAPH_EXPAND` column names are generated, so a dashboard or an agent writing SQL needs to know them, and which ones are
measures (readable only with `AGG`). The procedure turns the graph into a self-describing contract that CI can check
before anything queries it. Here it guards a real trap: summing product facings over scan rows says **64** facings;
the measure says **31**.

## How to run
```bash
python features/gcp/day-41-aisle-graph-schema/aisle_schema.py   # DuckDB + plain Python, writes result.json
pytest -q tests/test_day41_gcp_graph_expand_schema.py
GCP_PROJECT_ID=... bash features/gcp/day-41-aisle-graph-schema/cloud.sh   # in GitHub Actions (keyless)
```

## Cost
A temporary dataset (deleted on exit, tables expire in 1 h), three tiny INSERTs, the procedure call and one
`GRAPH_EXPAND` query (about 40 MiB minimum billing, as on day 37): well under a tenth of a cent on on-demand pricing.

## Result (local)
| Aisle | Products | Restock scans | Facings: SUM over scans | Facings: AGG (measure) | Units restocked |
|---|---|---|---|---|---|
| Bakery | 2 | 3 | 12 | **7** | 36 |
| Dairy | 3 | 5 | 25 | **13** | 72 |
| Produce | 2 | 4 | 27 | **11** | 119 |
| **Total** | 7 | 12 | 64 | **31** | 227 |

Schema contract: 12 columns, 3 measures, 7 described. Numbers in [`result.json`](result.json). Cloud result: see the
day's report (the merge commit's check-run annotations).
