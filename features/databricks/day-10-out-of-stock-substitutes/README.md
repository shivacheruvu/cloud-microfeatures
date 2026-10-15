# Day 10 · Databricks · Out-of-stock substitutes with the `NEAREST BY` join

**New platform feature:** the **`NEAREST BY` join** ("top-K nearest-neighbor queries": `JOIN target
{APPROX | EXACT} NEAREST k BY {DISTANCE | SIMILARITY} expression`, no `ON` clause), new in
[Databricks SQL 2026.20](https://docs.databricks.com/aws/en/sql/release-notes/2026): preview channel
**2026-06-29**; part of the current channel since. Reference:
[NEAREST BY clause](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-nearest-by).

## What it does
22 grocery items (category, cents per 100 g, pack size, organic), 5 of them out of stock. One read-only query over
inline `VALUES` returns the **two closest in-stock substitutes** for each out-of-stock item:

```sql
SELECT q.sku, t.sku, ... AS score
FROM oos q
INNER JOIN shelf t
  EXACT NEAREST 2 BY DISTANCE
    IF(q.category = t.category, 0, 1000) + abs(q.cents_per_100g - t.cents_per_100g)
      + abs(q.pack_g - t.pack_g) div 10 + 50 * abs(q.organic - t.organic)
```

The ranking expression is a "mismatch score" in whole points: 1000 for a different category, 1 per cent of unit
price difference, 1 per 10 g of pack size difference, 50 for organic vs. not. A first-choice score of 1000 or more
means the store has nothing close in that category.

## Why it matters
"For each X, the k closest Y" is the shape behind substitutions, nearest store, similar products and vector
recommendations. Before `NEAREST BY` you wrote the full cross join, a `row_number()` window and a `QUALIFY` filter
(that's exactly what the local DuckDB run does). `NEAREST BY` says what you mean in one join, and with `APPROX` the
optimizer is allowed to use a faster search for big candidate sets. The same clause takes
`vector_cosine_similarity(...)` over embeddings, so this substitute finder can later swap the hand-made score for
product-name embeddings without changing the query's shape.

## How to run
```bash
python features/databricks/day-10-out-of-stock-substitutes/substitutes.py --local        # DuckDB; writes result.json
python features/databricks/day-10-out-of-stock-substitutes/substitutes.py --sql          # print the Databricks SQL
python features/databricks/day-10-out-of-stock-substitutes/substitutes.py --databricks   # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day10_databricks_nearest_by.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions on a serverless SQL warehouse (the Statement Execution API).
It fails if any out-of-stock item's two substitutes, their order or their scores differ from the local cross-join
ranking. The test data has no ties at rank 2 vs. 3 (a test checks it), so `EXACT NEAREST 2` has one right answer.

## Cost
One small query on a serverless SQL warehouse (trial credits; also fine on Free Edition). Nothing is created.

## Result
| Out of stock | 1st substitute (score) | 2nd substitute (score) |
|---|---|---|
| organic oat milk | organic soy milk (5) | coconut milk drink (57) |
| organic muesli | organic granola (31) | muesli (57) |
| linguine | spaghetti (1) | penne (3) |
| dark roast beans | house blend beans (13) | organic espresso beans (90) |
| lime sparkling water | plain sparkling water (2) | almond milk (1172): no second sparkling water on the shelf |

All **5** first choices come from the same category; **1** second choice has to come from another category.
Full numbers in [`result.json`](result.json).
