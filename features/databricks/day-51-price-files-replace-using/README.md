# Day 51 · Databricks · Store price files kept current with a REPLACE USING flow

**New platform feature:** **REPLACE USING flows** in Spark Declarative Pipelines (Lakeflow pipelines), **Beta**,
Databricks Runtime 18.2 and above, classic or serverless compute
([Partial snapshot replacement with REPLACE USING flows](https://docs.databricks.com/aws/en/ldp/flows-replace-using);
the docs page was published **2026-09-17**, the date used here). The release-notes pages couldn't be read from this
unattended run (permission prompt timed out), so the date comes from the docs page's own metadata, read through its
[Azure Databricks copy](https://learn.microsoft.com/en-us/azure/databricks/ldp/flows-replace-using). Syntax follows
Databricks' official agent skill
([`databricks-pipelines/references/replace-using-flow-sql.md`](https://github.com/databricks/databricks-agent-skills/tree/main/skills/databricks-pipelines/references),
added 2026-11-20).

Not to be confused with day 6, which used the SQL statement `INSERT INTO ... REPLACE USING` on a Delta table. This is
the streaming flow type inside a pipeline: on each logical batch it deletes the target rows whose key matches and
inserts the new rows, with `SEQUENCE BY` deciding which batch is newest even when files arrive late or out of order.

## What it does
Three stores each send their **whole** shelf price file. The pipeline keeps one streaming table:

```sql
CREATE STREAMING TABLE shelf_prices
FLOW REPLACE USING (store_id) SEQUENCE BY file_version BY NAME
SELECT store_id, file_version, sku, price_cents
FROM STREAM(price_drops);
```

| Round | Files delivered | Rows delivered so far | `shelf_prices` rows | What must happen |
|---|---|---|---|---|
| 1 | S1 v1, S2 v1, S3 v1 | 14 | 14 | first load |
| 2 | S1 v2, S3 v2 | 23 | 14 | S1's corrected file **drops SKU 0903** (canned pumpkin recall) and fixes 2201 (4.99 → 4.49); S3 adds 0903 |
| 3 | S2 v2, **S1 v1 again (late)** | 33 | 14 | S2's holiday markdowns land; the late old S1 file is **ignored** |

A keep-latest-per-SKU upsert (SCD type 1 on `(store_id, sku)`) would end with 15 rows: it never removes S1's recalled
0903 because the corrected file simply doesn't mention it. REPLACE USING replaces the store's rows as a set.

## Why it matters
Store systems usually send a full price list, not change events. Getting "the newest file wins, as a whole" right by
hand means a MERGE plus a delete-not-matched-by-source step, plus your own guard against a late resend of an old file
overwriting a newer one. The flow does both declaratively and atomically per batch.

## How to run
```bash
python features/databricks/day-51-price-files-replace-using/price_files.py --local   # Python + DuckDB; writes result.json
python features/databricks/day-51-price-files-replace-using/price_files.py --sql     # pipeline.sql with the source filled in
pytest -q tests/test_day51_databricks_replace_using_flow.py
```
`cloud.sh` (GitHub Actions, `$DATABRICKS_HOST` / `$DATABRICKS_TOKEN`) creates a scratch schema with the `price_drops`
Delta table, uploads `pipeline.sql`, creates a serverless pipeline (PREVIEW channel, development mode), and for each
round appends that round's files, runs one update and compares `shelf_prices` with the local state. An inactive
workspace (compute refused) or the Beta not being available is a **warning**; any difference from local is an
**error**. The pipeline, the workspace folder and the schema are deleted on exit.

## Cost
Databricks Free Edition: three short serverless pipeline updates and a few warehouse statements on 33 rows. Nothing is
kept.

## Result
Local (pure Python and DuckDB agree after every round): **33 price rows in 6 file deliveries → 14 shelf prices**;
S1's recalled 0903 is gone, S1's late v1 resend is ignored, every store ends on file version 2.

Cloud: not run yet. The Free Edition workspace has refused compute since late October (`FEATURE_DISABLED` for jobs,
"could not be processed by the warehouse" for SQL), so this day is `built`; `cloud.sh` will run it once the workspace
is active again.
