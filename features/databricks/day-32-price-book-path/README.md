# Day 32 · Databricks · Switch the price book with `SET PATH`

**New platform feature:** the **`SET PATH` statement and `current_path()` function**, new in
[Databricks SQL 2026.43](https://docs.databricks.com/aws/en/sql/release-notes/2026) (Preview channel
**2026-10-19**, Current 2026-10-26; also Databricks Runtime 19+). `SET PATH` sets the ordered list of schemas used to
resolve unqualified table, view and function names
([reference](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-conf-mgmt-set-path)). It needs
no warehouse of its own, so it runs on a Free Edition serverless job; upstream it is in Apache Spark 4.3 behind
`spark.sql.path.enabled`, so the same SQL runs locally.

## What it does
[`price_path.py`](price_path.py) creates two price-book schemas:

| Schema | Objects |
|---|---|
| `cmf_day32_regular` | view `shelf_price(sku, product, price)`, SQL function `line_total(sku, price, qty)` = price × qty |
| `cmf_day32_ad_week` | only `line_total(sku, price, qty)` with this week's deals: eggs 2 for $5.00, oat milk 20% off, coffee $1.00 off |

and one basket report that never names a schema:

```sql
SELECT b.basket, SUM(b.qty) AS units, ROUND(SUM(line_total(b.sku, p.price, b.qty)), 2) AS total
FROM basket AS b JOIN shelf_price AS p USING (sku) GROUP BY b.basket ORDER BY b.basket
```

Only the path changes between runs:

```sql
SET PATH = system.session, <catalog>.cmf_day32_regular, SYSTEM_PATH   -- regular prices
SET PATH = <catalog>.cmf_day32_ad_week, PATH                          -- ad week first; shelf_price falls through
```

## Result (local reference; the cloud job must match it exactly)
| Basket | Units | Regular | Ad week | Saved |
|---|---|---|---|---|
| B1 | 10 | $26.80 | $23.38 | $3.42 |
| B2 | 6 | $34.24 | $30.66 | $3.58 |
| B3 | 12 | $34.08 | $32.16 | $1.92 |
| **total** | **28** | **$95.12** | **$86.20** | **$8.92** |

`current_path()` after each step (catalog shown as `<catalog>`, `system.ai` left out because open-source Spark has no
AI schema):

- regular: `system.session,<catalog>.cmf_day32_regular,system.builtin`
- ad week: `<catalog>.cmf_day32_ad_week,system.session,<catalog>.cmf_day32_regular,system.builtin`

Path rules checked on the way:

| Check | Outcome |
|---|---|
| `SET PATH = <catalog>.cmf_day32_missing, PATH` (schema doesn't exist) | accepted, skipped: same ad-week totals |
| `SET PATH = cmf_day32_regular, PATH` (one-part name) | `INVALID_SQL_PATH_SCHEMA_REFERENCE`, path unchanged |
| `SET PATH = SYSTEM_PATH, system.builtin` (duplicate after expansion) | `DUPLICATE_SQL_PATH_ENTRY`, path unchanged |
| `SET PATH = <catalog>.cmf_day32_regular, SYSTEM_PATH` (no `system.session`) | temp view `basket` not found (`TABLE_OR_VIEW_NOT_FOUND`) |

## Why it matters
The same report SQL (a dashboard, a saved query, a job) can run against the regular book, the ad-week book or a
store's override schema just by changing the session path. An override schema only has to hold what differs (here, one
function); everything else falls through to the next schema on the path. The last check is the gotcha: build a path
from `SYSTEM_PATH` and forget `system.session`, and your temp views and session variables disappear.

## How to run
```bash
python features/databricks/day-32-price-book-path/price_path.py     # local reference, writes result.json
pytest -q tests/test_day32_databricks_set_path.py                  # also runs the real SET PATH on local pyspark 4.3
bash features/databricks/day-32-price-book-path/cloud.sh            # needs DATABRICKS_HOST/TOKEN (CI)
```

The cloud run (REST only, no SQL warehouse) uploads `price_path.py`, submits it as a one-off serverless job on
environment version 6 with the workspace catalog, compares totals, paths and check outcomes with `result.json`, and
deletes the folder, the scratch volume and all three schemas. If the workspace refuses job runs (the Free Edition
account has been inactive since 2026-10-24), it reports a warning and verifies locally only.

## Cost
One short serverless job on Free Edition: no charge (within the daily quota).

## Limits of this slice
Small synthetic baskets. Views and SQL functions capture the path at creation; that capture isn't exercised here
(the report runs at session level), and `SQL SECURITY DEFINER` procedures aren't covered.
