# Day 4 · Databricks · Rolling grocery sales with metric view window measures

**New platform feature:** [Window measures in Unity Catalog metric views, generally available](https://docs.databricks.com/aws/en/metric-views/data-modeling/window-measures),
released **2026-10-06** ([release notes](https://docs.databricks.com/aws/en/release-notes/product/2026/october)).
A metric view measure can now carry a `window` (`order`, `range`, `semiadditive`): rolling, cumulative and
semiadditive measures are defined once in YAML instead of being re-written as window functions in every query.

## What it does
Four weeks of daily units for two stores (north was closed on 2026-09-09) go into a scratch table. A metric view
`store_sales_metrics` defines four measures:

| Measure | YAML | Meaning |
|---|---|---|
| `units_sold` | `SUM(units)` | units on the day |
| `units_prior_7d` | `SUM(units)`, `range: trailing 7 day` | the 7 calendar days **before** the day (exclusive is the default) |
| `open_days_prior_7d` | `COUNT(DISTINCT sale_date)`, `range: trailing 7 day` | trading days in that window |
| `units_mtd` | `SUM(units)`, `range: cumulative` | month to date, including the day |

```yaml
  - name: units_prior_7d
    expr: SUM(units)
    window:
      - order: sale_date
        range: trailing 7 day
        semiadditive: last
```

Two queries read the same measures at two grains with `MEASURE()`: per store and day, and per day across all
stores (the view re-aggregates, no new SQL). A day is flagged as a **spike** when it sells more than 1.4x its
prior-7-day average **per trading day**.

## Why it matters
"Last 7 days" and "month to date" are the most copied snippets in grocery reporting, and every copy picks its own
frame (`ROWS` vs `RANGE`, today in or out). Here the frame lives in one governed definition. Using a calendar
`RANGE` also gets gaps right: when north was closed, its next week has 6 trading days, not a zero day. Dividing by
7 would have flagged north's 2026-09-12 as a spike (1.44x); per trading day it's 1.24x, a normal Saturday.

## How to run
```bash
python rolling_sales.py --local        # DuckDB: the same measures as hand-written RANGE window functions; writes result.json
python rolling_sales.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN; serverless SQL warehouse
```
In CI, `cloud.sh` creates the schema `microfeatures_day04`, a 55-row table and the metric view, runs both queries
through the SQL Statement Execution API, fails unless every row equals the local result, then drops the view, table
and schema. The test (`tests/test_day04_databricks_metric_view_windows.py`) checks the local reference against a
plain-Python rolling sum, the closed-day gap, the spikes, and the metric view YAML.

## Cost
Seven small statements on a serverless SQL warehouse (seconds of compute plus idle time until auto-stop), covered by
the 14-day trial credits. Metric views run on serverless SQL warehouses, so this also works on Databricks Free Edition.

## Result (synthetic, seeded month)
| Store | Day | Units | Prior 7 days | Trading days | Per-day avg | Ratio | Spike |
|---|---|---|---|---|---|---|---|
| north | 2026-09-12 | 576 | 2,797 | 6 | 466 | 1.24 | no (1.44 if divided by 7) |
| north | 2026-09-18 | 781 | 3,198 | 7 | 457 | 1.71 | **yes** |
| south | 2026-09-12 | 636 | 2,178 | 7 | 311 | 2.04 | **yes** |
| south | 2026-09-25 | 507 | 2,305 | 7 | 329 | 1.54 | **yes** |

Month to date: 22,139 units (north 12,582, south 9,557). The three spikes are exactly the three promo days seeded
into the data.
