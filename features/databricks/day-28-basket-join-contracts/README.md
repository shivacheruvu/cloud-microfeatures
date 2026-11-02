# Day 28 · Databricks · Basket join contracts: metric view relationships, audited against the data

**New platform feature:** **relationships between metric views** (Beta): `ALTER VIEW <source_mv> ADD CONSTRAINT
<name> { MANY | ONE } TO { MANY | ONE } RELATIONSHIP TO <target_mv> { USING (...) | ON ... }` records how two Unity
Catalog metric views join (cardinality + keys), so AI assistants and BI tools pick the right join instead of guessing
from column names. Announced in the
[Databricks platform release notes, October 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/october)
on **2026-10-30** ([docs: Relationships between metric views](https://docs.databricks.com/aws/en/uc-semantics/metric-views/relationships)).
Requires a SQL warehouse (or DBR 19+), so it fits Free Edition's serverless warehouse.

## What it does
[`metric_views.sql`](metric_views.sql) builds three metric views over small grocery tables ([`data/`](data/)):
`basket_mv` (scanned lines: `row_count`, `revenue`), `products_mv` and `stores_mv`, then declares two joins:

```sql
ALTER VIEW basket_mv ADD CONSTRAINT basket_product MANY TO ONE RELATIONSHIP TO products_mv USING (barcode);
ALTER VIEW basket_mv ADD CONSTRAINT basket_store   MANY TO ONE RELATIONSHIP TO stores_mv   USING (store_id);
```

The docs are explicit that relationships are **metadata only: Databricks doesn't check the data against the declared
cardinality**. So [`contracts.py`](contracts.py) reads the relationships back (`DESCRIBE TABLE EXTENDED`, `# Constraints`
section; plus a count from `information_schema.table_constraints`) and audits each one with metric view queries
(`MEASURE(row_count)`, `MEASURE(revenue)` grouped by the join keys):

- **ONE side unique?** For `... TO ONE`, every target key must have one row, or a join fans out and double counts.
- **Orphans?** Every source key should find a target row, or an inner join silently drops it.

## Result (local reference = cloud check)
Basket revenue is **91.10** across 17 lines.

| Relationship | Declared | Finding | Impact |
|---|---|---|---|
| `basket_product` | basket_mv MANY TO ONE products_mv | barcode `0001` (oat milk) has **2** product rows (Plant-based *and* Dairy); barcode `0009` isn't in the product master | a join **overcounts revenue by 24.43**; an inner join drops 2.49 |
| `basket_store` | basket_mv MANY TO ONE stores_mv | store `S4` (3 lines) isn't in the store master yet | an inner join drops **14.27** |

0 of 2 declared relationships hold: exactly what a declaration alone would never tell you.

## Why it matters
Declaring the join is what lets Genie and dashboards stop guessing; auditing it is what stops them confidently
joining through a duplicated product row. The audit is generic: it reads whatever relationships are declared.

## How to run
```bash
python features/databricks/day-28-basket-join-contracts/contracts.py               # local, writes result.json
pytest -q tests/test_day28_databricks_metric_view_relationships.py
bash features/databricks/day-28-basket-join-contracts/cloud.sh                     # needs DATABRICKS_HOST/TOKEN (CI)
```

The cloud run creates schema `microfeatures_day28`, three tables, three metric views and two relationships on the
serverless SQL warehouse, compares the declared relationships and every audit number with the local run, and drops
the schema (`CASCADE`). If the workspace refuses compute, it reports a warning and verifies locally only.

## Cost
A dozen tiny statements on the Free Edition serverless warehouse: no charge on Free Edition (within its daily quota).

## Limits of this slice
Only `USING` relationships are audited (an `ON` condition would need expression parsing); the docs note that
relationships aren't exposed in the REST API or SDKs yet, so SQL is the only way to read them.
