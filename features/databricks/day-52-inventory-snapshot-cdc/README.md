# Day 52 · Databricks · On-hand history from nightly inventory snapshots with SQL `AUTO CDC ... FROM SNAPSHOT`

**New platform feature:** **snapshot CDC in SQL**: `FLOW AUTO CDC ... FROM SNAPSHOT (query) WITH VERSION (query)
KEYS (...) STORED AS SCD TYPE 2` in Lakeflow Spark Declarative Pipelines, with the `current_snapshot_version()` and
`last_snapshot_version()` functions (Databricks Runtime **18.3+**; before, snapshot CDC was Python-only through
`create_auto_cdc_from_snapshot_flow`). Documented in the
[AUTO CDC FLOW clause reference](https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-syntax-ddl-create-streaming-table-auto-cdc)
(page dated **2026-11-09**, the date recorded in the manifest; no stage label on the page;
[AWS docs](https://docs.databricks.com/aws/en/ldp/cdc)). Syntax follows Databricks'
[agent skills](https://github.com/databricks/databricks-agent-skills) (`databricks-pipelines/references/auto-cdc-from-snapshot.md`,
added 2026-11-20). The Databricks release-notes pages could not be read from this run (see the day's report).

## What it does
A store exports its **complete** on-hand count every night of Thanksgiving week (versions 1-4). There are no change
events, only whole snapshots. The pipeline ([`pipeline.sql`](pipeline.sql)) keeps a type 2 history per SKU:

```sql
CREATE OR REFRESH STREAMING TABLE inventory_history
FLOW AUTO CDC
FROM SNAPSHOT (SELECT sku, on_hand FROM inventory_snapshots
               WHERE snapshot_version = (SELECT version FROM current_snapshot_version()))
WITH VERSION (SELECT version FROM (SELECT DISTINCT snapshot_version AS version FROM inventory_snapshots)
              WHERE NOT EXISTS (SELECT 1 FROM last_snapshot_version())
                 OR version > (SELECT version FROM last_snapshot_version())
              ORDER BY version LIMIT 1)
KEYS (sku)
STORED AS SCD TYPE 2;
```

| Version | turkey | cranberry | stuffing | pie_crust | whipped |
|---|---|---|---|---|---|
| 1 | 40 | 120 | 80 | 60 | |
| 2 | 25 | 120 | 55 | 60 | 90 (new) |
| 3 | 25 | **95** | 55 | 60 | 90 |
| 4 | **6** | 95 | 55 | *(pulled)* | **40** |

Round 1 lands versions 1 and 2 before the first update (one update must work through both, in order); round 2 lands
3 and 4. The traps: an unchanged SKU must not get a new row (stuffing, 3 quiet nights), a SKU missing from the export
is a **delete** that closes its row (pie crusts at v4), and `snapshot_version` must stay out of the snapshot query's
output, or every row would look changed every night.

## Why it matters
Many store systems (POS back offices, warehouse exports, supplier price lists) can only give a full dump. Turning
dumps into history used to mean Python (`create_auto_cdc_from_snapshot_flow`) or a hand-written diff. Now it is one
SQL statement in a pipeline, and `WITH VERSION` makes late or batched snapshots process in order.

## How to run
```bash
python features/databricks/day-52-inventory-snapshot-cdc/snapshot_cdc.py --local   # Python + DuckDB; writes result.json
python features/databricks/day-52-inventory-snapshot-cdc/snapshot_cdc.py --sql     # the pipeline SQL
pytest -q tests/test_day52_databricks_snapshot_cdc.py
```
Locally the history is built twice: a plain Python diff of consecutive snapshots, and a DuckDB query (runs of equal
`on_hand` per SKU over versions, closed at the next version). Both must agree after each round.
`cloud.sh` (GitHub Actions, `$DATABRICKS_HOST` / `$DATABRICKS_TOKEN`) creates a scratch schema and Delta table, a
serverless pipeline on the PREVIEW channel in development mode, appends each round, runs one update per round and
compares `inventory_history (sku, on_hand, __START_AT, __END_AT)` with the local history. A refused workspace or the
feature not being available is a warning. The pipeline, workspace folder and schema are deleted on exit.

## Cost
Free Edition: two short serverless pipeline updates and a few SQL statements on the serverless warehouse; no paid
resources. Nothing is left running.

## Result
Local (Python and DuckDB agree): **18 snapshot rows over 4 nights → 10 history rows, 4 current**
(7 history rows / 5 current after round 1).

| sku | on_hand | __START_AT | __END_AT |
|---|---|---|---|
| cranberry | 120 | 1 | 3 |
| cranberry | 95 | 3 | |
| pie_crust | 60 | 1 | 4 (deleted) |
| stuffing | 80 | 1 | 2 |
| stuffing | 55 | 2 | |
| turkey | 40 | 1 | 2 |
| turkey | 25 | 2 | 4 |
| turkey | 6 | 4 | |
| whipped | 90 | 2 | 4 |
| whipped | 40 | 4 | |

Cloud: not run yet. The Free Edition workspace has refused compute since 10-30 (see the report), so this day is
`built` until it can run.
