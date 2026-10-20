# Day 15 · Databricks · Re-stage a corrected supplier cost file with CREATE OR REPLACE TEMP TABLE

**New platform feature:** **`CREATE OR REPLACE TEMP TABLE`** in
[Databricks SQL 2026.15](https://docs.databricks.com/aws/en/sql/release-notes/2026) (preview channel **2026-05-26**,
current 2026-06-03; also serverless environment 18.2, 2026-05-13): "`CREATE OR REPLACE TEMP TABLE` syntax is now
supported", so a session-local temporary table can be replaced in one statement instead of a `DROP` plus `CREATE`.

First day on **Databricks Free Edition** rules: serverless SQL warehouse only, nothing classic, nothing kept.

## What it does
A supplier sends today's unit costs for 12 SKUs, then re-sends a corrected file: in the first one three costs were
keyed in wrong (a digit dropped or doubled) and P12 was missing. Each delivery is staged in a temporary table and the
margin check runs against whatever is staged. The whole run is one SQL script, so every statement shares a session:

```sql
BEGIN
  CREATE OR REPLACE TEMP TABLE shelf (sku STRING, aisle STRING, price_cents INT);
  INSERT INTO shelf VALUES ...;
  CREATE OR REPLACE TEMP TABLE cost_batch (sku STRING, cost_cents INT, delivery INT);
  INSERT INTO cost_batch VALUES ...;            -- delivery 1 (bad)
  CREATE OR REPLACE TEMP TABLE cost_batch (...); -- the re-delivery: replaced in one statement, old rows gone
  INSERT INTO cost_batch VALUES ...;            -- delivery 2 (corrected)
  SELECT s.sku, aisle, cost_cents, price_cents, delivery,
         (price_cents - cost_cents) * 4 < price_cents AS below_target,   -- margin under 25%
         ... AS suggested_cents,                                          -- smallest price ending in 9 at >= 25%
         (SELECT COUNT(*) FROM cost_batch) AS batch_rows
  FROM shelf s JOIN cost_batch c ON c.sku = s.sku ORDER BY sku;
END
```
A second plain `CREATE TEMP TABLE cost_batch` would fail ("already exists"); `OR REPLACE` makes the staging step safe to
re-run. Temporary tables live only in the session and are dropped when it ends, so nothing is created in Unity
Catalog. Locally, plain Python computes the same rows, and the test runs the exact statements in DuckDB (which also
has `CREATE OR REPLACE TEMP TABLE`) and checks that a second plain `CREATE TEMP TABLE` fails there.

## Why it matters
Re-delivered files are routine (suppliers, POS exports, price feeds). Staging in a temp table keeps scratch data out
of the catalog, and `OR REPLACE` means a re-run or a corrected file just replaces the stage: no leftover rows from the
bad file, no `DROP TABLE IF EXISTS` boilerplate.

## How to run
```bash
python features/databricks/day-15-supplier-cost-restage/restage.py --local        # plain Python; writes result.json
python features/databricks/day-15-supplier-cost-restage/restage.py --sql          # print the SQL script
python features/databricks/day-15-supplier-cost-restage/restage.py --databricks   # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day15_databricks_temp_table_replace.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions on a serverless SQL warehouse (Statement Execution API) and
fails on any missing, extra or different row, or any row left over from the first delivery.

## Cost
One small SQL script on a serverless SQL warehouse (Free Edition: no charge). Only temporary tables, which end with
the session.

## Result
The re-delivery replaced the **11** staged rows with **12** (P02, P06, P09 corrected, P12 added). Against the corrected
costs, **7 of 12** SKUs are below the 25% margin target; pricing off the first file would have got **2** flags wrong
(P06 flagged by mistake, P09 missed).

| SKU | Cost | Shelf | Suggested |
|---|---|---|---|
| P01 | $3.16 | $3.89 | $4.29 |
| P03 | $3.29 | $4.29 | $4.39 |
| P05 | $2.44 | $2.99 | $3.29 |
| P07 | $2.62 | $3.29 | $3.59 |
| P08 | $1.50 | $1.99 | $2.09 |
| P09 | $3.79 | $4.79 | $5.09 |
| P10 | $4.09 | $4.99 | $5.49 (lowest margin, 18%) |

SKUs, costs and prices are made up. Full numbers in [`result.json`](result.json).
