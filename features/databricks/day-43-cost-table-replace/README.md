# Day 43 · Databricks · Weekly cost-table reload that keeps its documentation

**New platform feature:** **`CREATE OR REPLACE TABLE` preserves comments** (serverless compute release of
**2026-07-06**, Databricks Runtime 18 updates): "`CREATE OR REPLACE TABLE` now preserves existing column and table
comments by default. Previously, comments were dropped when recreating a table." From the
[serverless compute release notes](https://docs.databricks.com/aws/en/release-notes/serverless/) (July 6, 2026).

## What it does
`supplier_case_costs` holds the Harbor Farms weekly sheet (the six SKUs Google Cloud day 42 parses out of a PDF). It
is documented for Catalog Explorer and Genie: a table comment and a comment on every column (`case_cost`: "USD per
case, FOB warehouse", `units`: "Units per case" ...). Every Monday the loader rebuilds it from the new sheet with one
CTAS that also adds the unit cost and the week, and **says nothing about comments**:

```sql
CREATE OR REPLACE TABLE supplier_case_costs AS
SELECT sku, product, units, case_cost, round(case_cost / units, 2) AS unit_cost, 47 AS week
FROM VALUES ('HF-1001', 'Whole milk', 4, CAST(14.36 AS DECIMAL(8,2))), ... AS v(sku, product, units, case_cost)
```

[`table_replace.py`](table_replace.py) runs as a one-off serverless job (environment version 6): it creates the
documented Delta table in a scratch schema with the week 46 rows, snapshots `information_schema.tables` (comment),
`information_schema.columns` (comments) and the rows, runs the week 47 reload, snapshots again and reads the latest
`DESCRIBE HISTORY` operation.

**Must hold** (else the cloud run fails): after the reload the table comment and all 4 column comments are unchanged,
the table has the 6 new columns, and its rows equal the pure-Python reference (3 new case costs). **Reported:** the
Delta history operation, and any comment on the two new columns (none expected; one is a warning).

**Local contrast (in the test):** open-source Spark 4.3 without Delta can't `REPLACE` a parquet table, so the reload
there is a drop + CTAS: same rows, but the table comment and all column comments are gone, which is what a replace
did on Databricks before this release.

## Why it matters
Full-refresh loaders use `CREATE OR REPLACE TABLE ... AS SELECT` because it's atomic and keeps history. Until July it
also silently wiped the documentation people and Genie rely on, so teams re-ran `COMMENT ON` after every load or
gave up on documenting reloaded tables. Now the reload and the docs coexist.

## How to run
```bash
python features/databricks/day-43-cost-table-replace/table_replace.py    # reference, writes result.json
pytest -q tests/test_day43_databricks_table_replace.py                   # no credentials (local pyspark + fakes)
bash features/databricks/day-43-cost-table-replace/cloud.sh              # GitHub Actions: DATABRICKS_HOST / TOKEN
```

## Cost
One short serverless job (a CTAS over 6 rows and a few information_schema reads): within Free Edition's serverless
quota. Scratch schema (with the table), volume and workspace folder are deleted on exit.

## Result
Reference (local): 6 rows, 4 → 6 columns, 3 new case costs (HF-1001, HF-2002, HF-3001), week 47 total **$160.85**,
cheapest per unit **HF-3001** (large eggs, $2.39 per dozen); table comment and 4 column comments kept. Open-source
Spark 4.3: same rows, every comment gone after the drop + CTAS. The cloud run is refused while the Free Edition
account is inactive (see the report), so this is verified locally only for now. Numbers in [`result.json`](result.json).
