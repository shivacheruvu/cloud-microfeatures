# Day 40 · Databricks · Store-day sales materialized view, created and refreshed from serverless compute

**New platform feature:** **standalone pipelines on serverless general compute** (GA, **2026-11-11**): "Notebooks can
create and refresh standalone materialized views and streaming tables" by sending `CREATE` and `REFRESH` statements
with `spark.sql` from serverless general compute, with no SQL warehouse. From the
[Databricks platform release notes, November 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/november)
([requirements and limits](https://docs.databricks.com/aws/en/ldp/dbsql/compute)).

## What it does
Till receipts land in a `till_receipts` table; the morning report reads `store_day_sales`, a materialized view of
receipts, units and revenue per store per day. [`store_day_mv.py`](store_day_mv.py) runs as a one-off serverless job
(environment version 6, so Runtime 18.1+ as the feature needs):

1. create the table and load 10 receipts lines (3 stores, 2 days);
2. `spark.sql("CREATE OR REPLACE MATERIALIZED VIEW ... AS SELECT store, sale_date, COUNT(DISTINCT receipt), SUM(qty),
   SUM(qty * price_cents) ... GROUP BY store, sale_date")` and read it;
3. load 6 late receipts (store S03's 11-11 feed arrived a day late, two more S02 sales, 11-12 opens) and read the view
   again: **it must still give the first answer**, because a materialized view answers from what it stored;
4. `spark.sql("REFRESH MATERIALIZED VIEW ...")` (synchronous; async refresh isn't supported here) and read it a third
   time: it must now equal the new rollup.

[`run_cloud.py`](run_cloud.py) uploads the script over REST, submits the job, and requires all three reads to equal
the local reference ([`result.json`](result.json)); the schema (table, view, run volume) and the workspace folder are
deleted afterwards. Locally the reference is plain Python, cross-checked in the test by DuckDB running the view's own
`SELECT`.

## Why it matters
Until now a standalone materialized view needed a SQL warehouse (or a pipeline) to create and refresh it. On Free
Edition and other serverless-only workspaces, the notebook or job you already run can now own the view: load the
day's receipts, refresh the report view, carry on, all on the same serverless compute. The stale read in step 3 is the
reminder of what you're buying: fast reads of a stored answer, current only as of the last refresh.

## How to run
```bash
python features/databricks/day-40-store-day-mv-notebook/store_day_mv.py      # local reference, writes result.json
pytest -q tests/test_day40_databricks_standalone_mv_serverless.py            # no credentials needed
bash features/databricks/day-40-store-day-mv-notebook/cloud.sh               # GitHub Actions (DATABRICKS_HOST/TOKEN)
```

## Cost
One short serverless job (a table of 16 rows, one view, one refresh): a fraction of a DBU on the trial; free on Free
Edition. Everything it creates is deleted at the end.

## Result
Local reference: before the late receipts **5 store-days, 16 units, revenue 53.92**; after REFRESH **7 store-days,
25 units, revenue 85.99**; the refresh changes 3 store-days (S01 2026-11-12 and S03 2026-11-11 are new, S02 2026-11-11
grows from 1 receipt / 2 units to 3 receipts / 5 units). The cloud run needs the Free Edition workspace back (see the
day's report); until then it warns and the result is verified locally.
