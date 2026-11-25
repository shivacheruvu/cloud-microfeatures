# Day 50 · Databricks · Backfilling new shelf-tag columns with efficient column updates

**New platform feature:** **Efficient column updates** (Beta, **2026-11-13**), from the
[Databricks platform release notes, November 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/november)
(read on 2026-11-22; the pages could not be fetched again today)
([docs: efficient column updates](https://learn.microsoft.com/en-us/azure/databricks/tables/features/efficient-column-updates)).
With `'delta.enableColumnUpdates-preview' = 'true'` on a Unity Catalog managed Delta table (catalog commits, row
tracking, deletion vectors and column mapping in `id` mode), an `UPDATE` that fills a new column can write the new
values to separate column files instead of rewriting every data file with its unchanged columns. The statements follow
the docs as quoted in a public walkthrough
([ven-nbondala-tf/databricks-release-lab PR #20](https://github.com/ven-nbondala-tf/databricks-release-lab/pull/20)),
since the docs page itself could not be fetched in this unattended run.

## What it does
A 24-product Thanksgiving catalog (sku, name, long ingredient text, price) is loaded in two batches. The shelf-tag
team wants two new columns, filled with one `UPDATE`:

```sql
CREATE OR REPLACE TABLE <catalog>.cmf_day50_ecu.catalog_ecu (sku STRING, name STRING, ingredients STRING, price_cents INT)
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported', 'delta.enableRowTracking' = 'true',
               'delta.enableDeletionVectors' = 'true', 'delta.columnMapping.mode' = 'id',
               'delta.enableColumnUpdates-preview' = 'true');
-- two INSERT batches
ALTER TABLE ... ADD COLUMNS (ingredient_count INT, nut_warning BOOLEAN);
UPDATE ... SET ingredient_count = LENGTH(ingredients) - LENGTH(REPLACE(ingredients, ',', '')) + 1,
               nut_warning = (LOWER(ingredients) LIKE '%almond%' OR ... LIKE '%walnut%');
-- roll back: SET TBLPROPERTIES ('delta.enableColumnUpdates-preview' = 'false'); REORG TABLE ... APPLY (PURGE)
```

The same steps run on a plain control table (`catalog_std`). [`catalog_backfill.py`](catalog_backfill.py) holds the
data, the statements, a pure-Python reference and a DuckDB run of the same `ALTER` / `UPDATE`.

**Must hold** (else the cloud run fails): both tables' values equal the local reference, and the ECU table's values
are unchanged after the rollback. **Reported:** the `UPDATE`'s `DESCRIBE HISTORY` metrics on both tables (files
removed / added, bytes added). If the ECU table's update removed data files like the control, the run warns that
Databricks fell back to a standard update, which the docs allow (and a public test on 2026-11-18 saw on a workspace
without a Previews page). A refused workspace, or the property being rejected because the Beta is off, is a warning.

## Why it matters
Catalog tables grow columns all the time (a nut warning, a tag, an embedding), and the backfill is the expensive part:
a normal `UPDATE` rewrites every file, including the long text that did not change. Efficient column updates make a
backfill cost about as much as the new column. The control table and the history metrics show whether you actually
got that on your workspace, and the rollback steps show how to leave the Beta.

## How to run
```bash
python features/databricks/day-50-catalog-backfill-ecu/catalog_backfill.py         # local reference + DuckDB, writes result.json
python features/databricks/day-50-catalog-backfill-ecu/catalog_backfill.py --sql   # the Databricks statements
pytest -q tests/test_day50_databricks_efficient_column_updates.py                  # no credentials needed
bash features/databricks/day-50-catalog-backfill-ecu/cloud.sh                      # GitHub Actions (DATABRICKS_HOST/TOKEN)
```

Requirements: a workspace admin turns on Efficient Column Updates on the Previews page; Databricks Runtime 19+ (or a
current serverless SQL warehouse). Limits from the docs: `MERGE` does not use it; incompatible with write-time change
data feed and UniForm; not for Iceberg tables or Lakeflow pipelines; `DROP FEATURE` cannot remove it, so the rollback
keeps the feature in the table protocol. Use a scratch table, not a critical one.

## Cost
A few seconds on the serverless SQL warehouse: 24 rows, two small tables, about 20 statements. The scratch schema is dropped
at the end. Free Edition: no charge.

## Result
Local (pure Python and DuckDB agree): 24 products, 96 ingredients, 8 single-ingredient products, **8 nut warnings**
(pecan pie, green bean casserole with almonds, walnut halves, trail mix, cranberry walnut salad, hazelnut spread,
peanut butter, almond milk). Pumpkin pie lists nutmeg and is correctly *not* flagged. Numbers in
[`result.json`](result.json).

**Cloud:** not run yet: the Databricks Free Edition workspace still refuses compute (see the day's report). Status
`built`.
