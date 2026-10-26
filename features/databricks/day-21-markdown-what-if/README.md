# Day 21 · Databricks · Markdown what-if KPIs from a parameterised metric view

**New platform feature:** **Metric view parameters**, **Public Preview, announced 2026-06-26** in the
[Databricks platform release notes, June 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/june)
("Metric view parameters are now in Public Preview": parameters bind values at query time, so one definition serves
many query variants). References: [Use parameters with metric views](https://docs.databricks.com/aws/en/uc-semantics/metric-views/use-parameters),
[YAML reference: parameters](https://docs.databricks.com/aws/en/uc-semantics/metric-views/yaml-reference#parameters).
Querying needs Databricks SQL / DBR 18.2+; plain DDL on Unity Catalog, so it runs on the **Free Edition** serverless
SQL warehouse.

## What it does
A week of grocery sales (13 lines across produce, bakery and dairy, with shelf price and unit cost) behind one metric
view with a `markdown` parameter:

```yaml
version: 1.1
parameters:
  - name: markdown
    data_type: double
    default: 0
source: microfeatures_day21.week_sales
fields:
  - name: department
    expr: department
measures:
  - name: net_sales
    expr: SUM(ROUND((1 - markdown) * shelf_price, 2) * units)
  - name: margin
    expr: SUM((ROUND((1 - markdown) * shelf_price, 2) - unit_cost) * units)
  - name: lines_below_cost
    expr: COUNT_IF(ROUND((1 - markdown) * shelf_price, 2) < unit_cost)
```

The caller binds the markdown when querying the view as a table-valued function:

```sql
SELECT department, MEASURE(net_sales), MEASURE(margin), MEASURE(lines_below_cost)
FROM microfeatures_day21.markdown_metrics(markdown => 0.2)
GROUP BY ALL;
```

Leaving the argument out (`FROM microfeatures_day21.markdown_metrics`) uses the default, 0 (shelf price).

## Why it matters
"What if we ran 10% / 20% / 30% off?" used to mean one metric view per scenario, or hand-written SQL that drifts from
the governed definition. With a parameter, the definition of net sales, margin and "below cost" lives once in Unity
Catalog and every scenario is just an argument. The answer here: dairy (milk and eggs are near cost) can't go past
10%, produce past 20% (avocados), bakery is safe even at 30%.

## How to run
```bash
python features/databricks/day-21-markdown-what-if/markdown_what_if.py --local        # DuckDB; writes result.json
python features/databricks/day-21-markdown-what-if/markdown_what_if.py --databricks   # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day21_databricks_metric_view_parameters.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions: it creates a scratch schema, the sales table and the metric
view, queries the view at `markdown => 0.0 / 0.1 / 0.2 / 0.3` and once with no argument, checks every row equals the
local DuckDB run (the same measures written by hand), and drops the view, table and schema. It fails if the warehouse
refuses the `parameters` block or the TVF call.

## Cost
Eight small statements on the Free Edition serverless SQL warehouse (no charge). One tiny table and one view, both
dropped in the same run.

## Result
| Markdown | Net sales | Margin | Lines below cost |
|---|---|---|---|
| 0% (default) | $5,527.02 | $1,836.85 | 0 |
| 10% | $4,972.34 | $1,282.17 | 0 |
| 20% | $4,417.66 | $727.49 | 2 (milk, eggs) |
| 30% | $3,862.98 | $172.81 | 3 (+ avocados) |

Deepest tested markdown with no line below cost: **bakery 30%, produce 20%, dairy 10%**.
