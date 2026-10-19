# Day 14 · Databricks · Current shelf prices and aisle top sellers with QUALIFY

**New platform feature:** the **`QUALIFY`** clause in
[Databricks SQL 2026.20](https://docs.databricks.com/aws/en/sql/release-notes/2026) (preview channel **2026-06-29**,
current 2026-07-13): "SQL queries now support a `QUALIFY` clause, which lets you filter on window function results
directly in the query."

Day 14 is also the last day of the Databricks trial. The query is read-only and runs on a serverless SQL warehouse, so
it carries over to Free Edition unchanged.

## What it does
Shelf-tag price changes arrive as an event log: 87 rows of (store, sku, effective date, price) for 11 SKUs in 3 stores
over Aug–Oct 2026. Sales are units per store and SKU in 4 aisles. One read-only query over inline `VALUES` answers two
questions, each with a `QUALIFY` instead of a subquery:

```sql
-- the price on the shelf today: newest tag per store and SKU
SELECT store, sku, price_cents, effective_date,
       COUNT(*) OVER (PARTITION BY store, sku) AS n,                                      -- price changes so far
       LAG(price_cents) OVER (PARTITION BY store, sku ORDER BY effective_date) AS prev    -- price before this one
FROM tags
QUALIFY ROW_NUMBER() OVER (PARTITION BY store, sku ORDER BY effective_date DESC) = 1;

-- the 2 best sellers per aisle (RANK keeps ties), over units = SUM(qty) GROUP BY aisle, sku
SELECT aisle, sku, units, RANK() OVER (PARTITION BY aisle ORDER BY units DESC) AS rnk
FROM units
QUALIFY RANK() OVER (PARTITION BY aisle ORDER BY units DESC) <= 2;
```
(The first version ranked `SUM(qty)` directly inside `QUALIFY` in the same grouped SELECT. DuckDB accepts that;
Databricks does not: "QUALIFY does not support aggregate functions". Fixed forward on 2026-10-18 with a `GROUP BY`
step first.)
The windows in the select list are evaluated before `QUALIFY` filters, so the surviving row still knows how many
changes there were and what the previous price was. Locally, plain Python computes the same rows, and the test runs
the exact query in DuckDB (which also has `QUALIFY`); the cloud result must match row for row.

## Why it matters
"Latest row per key" is the most common query on any change log (current price, current address, last scan), and
"top N per group" is the next. Without `QUALIFY` both need a subquery or CTE whose only job is to compute a row number
so the outer query can filter on it.

## How to run
```bash
python features/databricks/day-14-current-shelf-prices/shelf.py --local        # plain Python; writes result.json
python features/databricks/day-14-current-shelf-prices/shelf.py --sql          # print the Databricks SQL
python features/databricks/day-14-current-shelf-prices/shelf.py --databricks   # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day14_databricks_qualify.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions on a serverless SQL warehouse (Statement Execution API) and
fails on any missing, extra or different row.

## Cost
One small read-only query on a serverless SQL warehouse (trial credits; also fine on Free Edition). Nothing is
created.

## Result
`QUALIFY` keeps **33** of 87 shelf-tag rows (one per store and SKU). 24 store/SKUs have changed price at least once;
for **12** of them the latest change was a cut. P07 has the widest spread across stores: **120 cents**
(S2 $2.29, S1 $2.99, S3 $3.49).

| Aisle | Top sellers (units, rank) |
|---|---|
| bakery | P11 66 (1), P10 42 (2) |
| dairy | P01 123 (1), P03 78 (2) |
| pantry | P08 105 (1), P09 63 (2) |
| produce | P04 231 (1), P05 90 (2), P06 90 (2): the tie is kept by `RANK` |

Stores, prices and sales are made up. Full numbers in [`result.json`](result.json).
