# Day 20 · Databricks · Unique barcodes with informational `UNIQUE` constraints

**New platform feature:** **`UNIQUE` constraints** on Unity Catalog Delta tables, **Public Preview, announced
2026-06-05** in the [Databricks SQL release notes 2026](https://docs.databricks.com/aws/en/sql/release-notes/2026)
("Informational constraints on Unity Catalog Delta tables, which foreign keys can reference"). References:
[CONSTRAINT clause](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-table-constraint),
[Constraints on Databricks](https://docs.databricks.com/aws/en/tables/constraints),
[REFERENTIAL_CONSTRAINTS](https://docs.databricks.com/aws/en/sql/language-manual/information-schema/referential_constraints).
Databricks SQL and DBR 18.2+, Unity Catalog only, no enablement step: it runs on the **Free Edition** serverless SQL
warehouse. Before this, a foreign key could only reference a primary key.

## What it does
The catalog keeps a surrogate primary key, and the barcode is a second, nullable key (loose produce has none):

```sql
CREATE TABLE products (
  product_id BIGINT NOT NULL, upc STRING, name STRING,
  CONSTRAINT products_pk PRIMARY KEY (product_id),
  CONSTRAINT products_upc_uq UNIQUE (upc));

CREATE TABLE scans (
  scan_id BIGINT NOT NULL, upc STRING, qty INT, price DECIMAL(6, 2),
  CONSTRAINT scans_pk PRIMARY KEY (scan_id),
  CONSTRAINT scans_upc_fk FOREIGN KEY (upc) REFERENCES products (upc));   -- FK to the UNIQUE key
```

Register scans only know the barcode, so the foreign key now points where the data actually joins. The constraints
are **informational**: Unity Catalog records them (BI tools and the optimizer can read them), but Databricks does not
reject rows that break them. So the feature also ships the audit that does the enforcing (`keys.sql`):

* `duplicate_upcs`: barcodes listed more than once (NULLs don't clash, as the docs say)
* `orphan_scans`: scans whose barcode isn't in the catalog (NULL barcode = weighed produce, allowed)
* `join_fanout`: what a duplicate costs a sales report that joins scans to the catalog on the barcode

The sample catalog has 10 products; a supplier feed relisted the eggs and the bakery feed relisted the sourdough under
the same barcodes. 12 scans, two of them weighed produce and one with a barcode the catalog doesn't know.

## Why it matters
A catalog join on a duplicated barcode silently multiplies rows: here 10 barcode scans become **13** joined rows and
the day's sales read **$87.56 instead of $59.72** (+$27.84). Declaring `UNIQUE (upc)` documents the intent in Unity
Catalog, lets the FK say what scans really reference, and gives the audit a contract to check. An enforcing engine
(DuckDB with a real `UNIQUE`) would refuse products 8 and 10 at insert time; Databricks accepts them, which is exactly
why the audit has to run.

## How to run
```bash
python features/databricks/day-20-unique-barcodes/barcodes.py --local        # DuckDB; writes result.json
python features/databricks/day-20-unique-barcodes/barcodes.py --databricks   # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day20_databricks_unique_constraints.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions: it creates a scratch schema with both tables and their
constraints, loads the data (the duplicate barcodes must be accepted), runs the three audit queries, reads
`information_schema.table_constraints` and `referential_constraints`, and drops everything. It fails if the DDL is
refused, if the audit differs from the local DuckDB run, or if the information schema doesn't show the `UNIQUE`
constraint with `scans_upc_fk` referencing `products_upc_uq`.

## Cost
About a dozen small statements on the Free Edition serverless SQL warehouse (no charge). Two tiny tables are created
and dropped in the same run.

## Result
| Check | Result |
|---|---|
| Duplicate barcodes | `041303001127` (products 3, 8: eggs + supplier relist), `072250011372` (7, 10: sourdough + bakery feed) |
| Products without a barcode | 2 (bananas, apples; allowed) |
| Orphan scans (break the FK) | scan 110, barcode `085239311004` |
| Barcode scans → joined rows | 10 → **13** |
| Sales: true vs after the barcode join | **$59.72** vs **$87.56** (+$27.84) |
| An enforcing engine would refuse | products 8 and 10 |

Full numbers in [`result.json`](result.json).
