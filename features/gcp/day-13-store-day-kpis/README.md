# Day 13 · Google Cloud · Store-day KPIs with multi-level aggregation

**New platform feature:** **multi-level aggregation** in GoogleSQL (Preview,
[BigQuery release notes, 2026-07-08](https://docs.cloud.google.com/bigquery/docs/release-notes)): an aggregate
function can take another aggregate function as its argument, with its own `GROUP BY` (and optional `HAVING`) inside
the call. Reference: [multi-level aggregation](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/aggregate-function-calls#multi_level_aggregation).

## What it does
Three stores, two weeks (Mon 2026-09-28 to Sun 2026-10-11), 208 baskets and 458 basket lines over six SKUs, each
store with its own shelf prices (in cents). One read-only query, `GROUP BY store`, computes KPIs that each need
two levels of aggregation:

```sql
AVG(SUM(revenue) GROUP BY sale_date)                                  AS avg_daily_revenue,
MAX(SUM(revenue) GROUP BY sale_date)                                  AS best_day_revenue,
AVG(SUM(IF(is_weekend, revenue, NULL)) GROUP BY sale_date)           AS avg_weekend_day_revenue,
AVG(SUM(revenue) GROUP BY basket_id)                                  AS avg_basket_value,
AVG(SUM(qty) GROUP BY basket_id)                                      AS avg_items_per_basket,
AVG(ANY_VALUE(shelf_price) GROUP BY sku)                              AS avg_shelf_price,   -- once per SKU
AVG(shelf_price)                                                      AS naive_avg_shelf_price
```
Locally, DuckDB computes the same KPIs the old way (a CTE per inner level, joined back), and the test recomputes
them in pure Python. In the cloud, every KPI must match the local value to 0.01 cents.

**Fix forward (2026-10-17):** the first cloud run wrote the weekend KPI as
`AVG(SUM(revenue) GROUP BY sale_date, is_weekend HAVING is_weekend)`, after the reference page's inner-`HAVING`
example, and BigQuery rejected it: "Function avg has both having expression and group by list. This is not
supported in multi-level aggregation." The weekend filter now goes inside the inner `SUM` instead: weekday sums
are NULL and the outer `AVG` skips them, which gives the same number.

## Why it matters
"Average daily revenue per store" is a sum per day, then an average per store. Before, that was one CTE per inner
grouping (day, basket, SKU) and a join to bring them back together, and it's easy to get wrong: a plain
`AVG(shelf_price)` over the sales join counts each price once per line sold, so cheap, popular bananas drag the
"average shelf price" down by 77 to 133 cents. With multi-level aggregation all seven KPIs sit in one `SELECT`, and
`AVG(ANY_VALUE(shelf_price) GROUP BY sku)` counts each SKU once.

## How to run
```bash
python features/gcp/day-13-store-day-kpis/kpis.py --local     # DuckDB nested GROUP BYs; writes result.json
python features/gcp/day-13-store-day-kpis/kpis.py --sql       # print the BigQuery SQL (data inlined)
pytest -q tests/test_day13_gcp_multi_level_aggregation.py
```
`cloud.sh` runs the query in GitHub Actions (keyless) with `bq` and fails if a store is missing or any KPI differs
from local.

## Cost
One query over about 50 KB of inlined data, capped at 20 MiB billed (BigQuery free tier). Nothing is created.

## Result
| Store | Baskets | Avg day | Best day | Avg weekend day | Weekend lift | Avg basket | Items / basket | Avg shelf price (per SKU) | Plain AVG over the join |
|---|---|---|---|---|---|---|---|---|---|
| S1 Downtown Market | 88 | $74.52 | $124.80 | $80.53 | +8.1% | $11.86 | 3.35 | $4.81 | $3.48 |
| S2 Riverside Fresh | 70 | $70.32 | $173.61 | $118.44 | +68.4% | $14.06 | 3.94 | $4.77 | $3.65 |
| S3 Southgate Grocer | 50 | $52.82 | $92.33 | $49.60 | −6.1% | $14.79 | 3.68 | $4.66 | $3.89 |

Riverside Fresh does most of its business at the weekend; Southgate's weekend days are slightly below its average.
Data is made up. Full numbers in [`result.json`](result.json).
