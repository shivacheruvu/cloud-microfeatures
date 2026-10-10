# Day 5 · Databricks · Fiscal-week sales with numeric-index metric view windows

**New platform feature:** [Window measures on numeric index columns in metric views](https://docs.databricks.com/aws/en/metric-views/data-modeling/window-measures),
released **2026-09-14** ([release notes](https://docs.databricks.com/aws/en/release-notes/product/2026/september)).
A metric view window can now be ordered by a dense **integer** column (fiscal weeks, 4-4-5 periods) with unitless
`offset` and `range` (`offset: -1`, `trailing 4`), instead of calendar intervals on a date. Needs YAML version 1.1+.

## What it does
Thirteen fiscal weeks of units for two stores, running from FY2026 week 49 across the year end into FY2027 week 9,
go into a scratch table. Each week has a dense `week_index` (1..13) from the fiscal calendar, so FY2027 W01 sits
right after FY2026 W52. A metric view `weekly_sales_metrics` defines:

| Measure | YAML | Meaning |
|---|---|---|
| `units_sold` | `SUM(units)` | units in the week |
| `units_prev_week` | `range: current`, `offset: -1` | the week before (one index position back) |
| `units_prior_4wk` | `range: trailing 4` | the 4 weeks **before** the week (exclusive is the default) |
| `units_to_date` | `range: cumulative` | everything up to and including the week |

```yaml
  - name: units_prev_week
    expr: SUM(units)
    window:
      - order: week_index
        range: current
        offset: -1
        semiadditive: last
```

Two queries read the measures with `MEASURE()` per store and week, and across stores. A week is a **spike** when
it sells more than 1.25x the average of the 4 weeks before it. The local run checks the index is dense first
(the docs warn that gaps or repeats give wrong numbers without an error).

## Why it matters
Grocery retailers report on fiscal weeks, not calendar dates, and week-of-year arithmetic breaks at the year end:
`fiscal_week - 1` for FY2027 W01 is week 0, which doesn't exist. On the dense index, last week of FY2027 W01 is
FY2026 W52 (4,983 units across stores), with no special case. Comparing against a 4-week base rather than last
week also avoids a false alarm: south's FY2027 W01 is up **42% on the week** only because W52 was a holiday dip;
against its prior 4-week average it's 1.14x, a normal week.

## How to run
```bash
python fiscal_weeks.py --local        # DuckDB: the same measures as integer RANGE window functions; writes result.json
python fiscal_weeks.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN; serverless SQL warehouse
```
In CI, `cloud.sh` creates the schema `microfeatures_day05`, a 26-row table and the metric view, runs both queries
through the SQL Statement Execution API, fails unless every row equals the local result, then drops the view, table
and schema. The test (`tests/test_day05_databricks_fiscal_week_index.py`) checks the local reference against plain
Python, the year-boundary step, the density check, the spikes and the metric view YAML.

## Cost
Seven small statements on a serverless SQL warehouse (seconds of compute plus idle time until auto-stop), covered by
the 14-day trial credits. Metric views run on serverless SQL warehouses, so this also works on Databricks Free Edition.

## Result (synthetic, seeded quarter)
| Store | Week | Units | Last week | Prior 4 weeks | Ratio to 4-wk avg | Spike |
|---|---|---|---|---|---|---|
| north | FY2027 W03 | 4,525 | 3,187 | 12,855 | 1.41 | **yes** |
| south | FY2026 W52 | 1,740 | 2,392 | 6,944 | 1.00 | no (holiday dip, -27% on the week) |
| south | FY2027 W01 | 2,476 | 1,740 | 8,684 | 1.14 | no (+42% on the week) |
| south | FY2027 W06 | 3,229 | 2,301 | 9,282 | 1.39 | **yes** |

Units to date: 74,222 (north 43,165, south 31,057). The two spikes are exactly the two promo weeks seeded into the data.
