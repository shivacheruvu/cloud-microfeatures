# Day 6 · Databricks · Re-load a corrected scan export with INSERT ... REPLACE USING / REPLACE ON

**New platform feature:** [`INSERT INTO ... REPLACE USING` and `REPLACE ON`](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-dml-insert-into),
added in **Databricks SQL 2026.32** (preview channel 2026-08-11, current channel **2026-08-24**;
[Databricks SQL release notes](https://docs.databricks.com/aws/en/sql/release-notes/2026)).
One atomic statement deletes the target rows that match the incoming batch, then inserts the batch:
`REPLACE USING (cols)` matches on equality of the listed columns, `REPLACE ON <condition>` on any condition that
can reference both the table and the batch.

## What it does
A store's POS re-sends a day of scans after a fix. Three days x two stores x four products go into a scratch table,
plus two problems in north's 2026-10-02 export: the milk and eggs lines were **sent twice**, and bananas were
undercounted by 4. There is also one **undated** shrink adjustment row (`scan_date` NULL). The corrected export
holds north 2026-10-02's four clean lines and its own undated adjustment.

Two copies of the table each get the correction loaded **twice**, to check a rerun changes nothing:

```sql
-- equality on (store, scan_date): NULL never matches
INSERT INTO sales_using REPLACE USING (store, scan_date) SELECT * FROM correction;

-- null-safe: <=> treats NULL = NULL as a match
INSERT INTO sales_on AS t
  REPLACE ON t.store <=> s.store AND t.scan_date <=> s.scan_date
  (SELECT * FROM correction) AS s;
```

A snapshot (lines, units and cents per store and day) is taken after each run.

## Why it matters
Re-loading a corrected day used to take a `DELETE` plus an `INSERT` (two commits, a window where the day is missing)
or a `MERGE` with a key the scan lines don't have. `REPLACE USING` does it in one atomic statement keyed on the
partition you're reloading. The catch the docs mention and this run shows: **a NULL in a `USING` column never
matches**, so every rerun appends the undated adjustment again (1 → 2 → 3 rows; north's undated shrink grows from
-3 to -13 units). `REPLACE ON` with `<=>` fixes that and makes the reload idempotent.

## How to run
```bash
python reload.py --local        # DuckDB: the documented semantics as delete+insert in one transaction; writes result.json
python reload.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN; serverless SQL warehouse
```
In CI, `cloud.sh` creates the schema `microfeatures_day06` with four small tables, runs each statement twice through
the SQL Statement Execution API, fails unless all five snapshots equal the local result, then drops the tables and
schema. The test (`tests/test_day06_databricks_replace_using.py`) checks the local run against a plain-Python model
of both clauses and checks the SQL text.

## Cost
About 15 small statements on a serverless SQL warehouse (seconds of compute plus idle time until auto-stop),
covered by the 14-day trial credits. The clauses work on serverless SQL warehouses, so this also runs on
Databricks Free Edition.

## Result (synthetic scans)
| | Lines | Units | Undated rows | Rerun changes nothing? |
|---|---|---|---|---|
| Base table | 27 | 1,264 | 1 | |
| `REPLACE USING`, run 1 | 26 | 1,136 | 2 | |
| `REPLACE USING`, run 2 | 27 | 1,131 | 3 | **no** |
| `REPLACE ON ... <=>`, run 1 | 25 | 1,139 | 1 | |
| `REPLACE ON ... <=>`, run 2 | 25 | 1,139 | 1 | **yes** |

North 2026-10-02 goes from 6 lines / 356 units / $1,073.04 to 4 lines / 233 units / $617.37 with either clause.
