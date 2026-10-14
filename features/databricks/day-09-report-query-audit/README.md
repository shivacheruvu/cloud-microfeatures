# Day 9 · Databricks · Which saved report queries still parse, and what they read: `parse_sql`

**New platform feature:** the SQL function **`parse_sql`**, which "parses a SQL statement and returns structured
JSON": statement metadata, table references and select-list expressions, or the parser error. New in
[Databricks SQL 2026.36](https://docs.databricks.com/aws/en/sql/release-notes/2026): preview channel
**2026-08-31**, current channel 2026-09-08.

## What it does
A grocery team keeps eight saved report queries (weekly revenue, top products, promo lift, stock-outs, basket
size, and three that were edited by hand and never re-run). One read-only statement parses all of them on a
serverless SQL warehouse:

```sql
SELECT name, CAST(parse_sql(query) AS STRING) AS parsed
FROM VALUES ('weekly_revenue', 'SELECT store_id, ... FROM scans ...'), ... AS t(name, query);
```

`parse_sql` only parses: none of the queries run, and none of the tables (`scans`, `products`, `promotions`,
`inventory`, `stores`) have to exist. From the JSON the audit reads two things per query: **does it parse**, and
**which tables does it read** (a CTE name such as `promo` is not a table). Put together, that's a table → report
lineage: what breaks if `scans` changes.

## Why it matters
Broken saved queries are usually found by the person who opens the dashboard on Monday. Before `parse_sql` you had
to run each query (cost, side effects, missing tables) or ship a third-party parser whose dialect doesn't match
Databricks SQL. Now the warehouse's own parser answers in one cheap statement.

## How to run
```bash
python features/databricks/day-09-report-query-audit/audit.py --local        # DuckDB parser; writes result.json
python features/databricks/day-09-report-query-audit/audit.py --sql          # print the Databricks SQL
python features/databricks/day-09-report-query-audit/audit.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN
pytest -q tests/test_day09_databricks_parse_sql.py
```
The expected verdict and tables for each query are declared in [`data/reports.csv`](data/reports.csv). Locally,
DuckDB's `json_serialize_sql` must agree with them. In the cloud, `cloud.sh` runs `parse_sql` in GitHub Actions and
fails if a query's verdict differs or a parsed query's output doesn't name one of its tables. The function
reference page wasn't reachable when this was built, so the check reads the JSON defensively (any non-empty
error-like key or parser-error text means broken; tables are matched as whole words) and the run prints the
top-level keys `parse_sql` returns as a notice, to pin the shape down.

## Cost
One small query on a serverless SQL warehouse (trial credits; also fine on Free Edition). Nothing is created.

## Result
| Query | Parses | Tables read |
|---|---|---|
| weekly_revenue | yes | scans |
| top_products | yes | products, scans |
| promo_lift | yes | promotions, scans (CTE `promo` excluded) |
| stockouts | yes | inventory, stores |
| basket_size | yes | scans (through a subquery) |
| revenue_by_store_v2 | **no**: `sum(units FROM` is missing its `)` | |
| lapsed_barcodes | **no**: `WHERE units >` has no right-hand side | |
| price_list | **no**: `SELEC` | |

**3 of 8** saved report queries no longer parse. The other 5 read 5 tables; `scans` feeds 4 of them.
Full numbers in [`result.json`](result.json).

## Fix forward (same day, after the first cloud run)
The first post-merge run (PR #35) matched local on 7 of 8 queries. The odd one was `SELECT barcode FROM scans WHERE`:
DuckDB rejects it, but **`parse_sql` returned `parse_success: true`**, so Databricks SQL accepts it, presumably
reading `WHERE` as a table alias. A real dialect difference, and a good reason to lint with the warehouse's own
parser. The test query became `... WHERE units >`, which neither parser accepts. The run also showed the output
shape: a JSON list with one object per statement (`start`, `length`, `parse_success`, and on failure `error` with
`errorClass: PARSE_SYNTAX_ERROR`, `sqlState: 42601` and the hint, e.g. "missing ')'"). The check now reads
`parse_success` directly.
