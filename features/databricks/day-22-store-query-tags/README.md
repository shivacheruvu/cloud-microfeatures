# Day 22 · Databricks · Which store is the warehouse working for? Query tags on every report

**New platform feature:** **query tags for SQL warehouses** (GA), custom key/value tags on SQL workloads for grouping
and cost attribution, recorded in `system.query.history.query_tags`. Announced in the
[Databricks release notes, September 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/september)
on **2026-09-30** ([how-to](https://docs.databricks.com/aws/en/sql/user/queries/query-tags)).

## What it does
Three stores (north, south, east) each get a "daily close" report: units, net sales and lines per department. Each
report is sent through the Statement Execution API with **statement-level tags**:

```json
"query_tags": [{"key": "store", "value": "north"}, {"key": "report", "value": "daily_close"},
               {"key": "feature", "value": "day22"}]
```

Then one query reads the tags back and attributes the work per store:

```sql
SELECT query_tags['store'] AS store, query_tags['report'] AS report,
       COUNT(*) AS statements, SUM(produced_rows) AS produced_rows
FROM system.query.history
WHERE statement_id IN (...) AND query_tags['feature'] = 'day22'
GROUP BY ALL ORDER BY store
```

Tags are checked against the documented limits before they're sent (at most 20 per query, keys and values up to
128 characters, no `, : - / = .` in keys, no `@@` prefix), since an invalid key fails the statement.

## Why it matters
On a shared warehouse every store's reports, dashboards and ad-hoc queries look the same in the bill. A tag on the
statement, rather than a naming convention in the SQL text, makes "how much of this week's warehouse time was the
south store's?" a `GROUP BY` over `system.query.history`. It also costs nothing: no extra tables or jobs.

## How to run
```bash
python features/databricks/day-22-store-query-tags/store_query_tags.py --local        # DuckDB; writes result.json
python features/databricks/day-22-store-query-tags/store_query_tags.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN
pytest -q tests/test_day22_databricks_query_tags.py
```
`cloud.sh` (GitHub Actions) creates a scratch schema and table, runs the three tagged reports, fails if any differs
from the local DuckDB run, then polls `system.query.history` for up to ~6 minutes (it isn't real-time) and fails if
the tag rollup isn't one `daily_close` statement per store with the right row counts. If the history table can't be
read (it needs an account admin grant) or the rows haven't arrived yet, the run says so as a warning instead of
failing, because the reports themselves were verified. Table and schema are dropped on exit.

## Cost
Four tiny statements and one system-table read on a serverless SQL warehouse; fine on Free Edition.

## Result
| Store | Units | Net sales | Top department | Tagged statements | Rows produced |
|---|---|---|---|---|---|
| east | 830 | $2,542.37 | dairy | 1 | 3 |
| north | 1,048 | $2,538.57 | dairy | 1 | 3 |
| south | 1,357 | $2,511.63 | dairy | 1 | 3 |

17 sales lines, three tags per statement. Full numbers in [`result.json`](result.json).
