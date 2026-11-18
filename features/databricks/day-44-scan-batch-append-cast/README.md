# Day 44 · Databricks · Store scan batches with narrower types append without hand-written casts

**New platform feature:** **DataFrame appends cast type-compatible columns** (serverless compute release of
**2026-07-06**, Databricks Runtime 18 updates): "`writeTo().append()`, `writeTo().overwrite()`,
`writeTo().overwritePartitions()`, and `write.mode("append").saveAsTable()` now cast type-compatible columns to the
target schema instead of failing with `DELTA_FAILED_TO_MERGE_FIELDS`." From the
[serverless compute release notes](https://docs.databricks.com/aws/en/release-notes/serverless/) (July 6, 2026).

## What it does
`store_scans` is the chain-wide scan table: `store_id INT, sku STRING, qty BIGINT, unit_price DECIMAL(10,2),
scan_date DATE`. Two stores send batches with narrower types:

| Batch | Types | Written with |
|---|---|---|
| Store 7 (old POS), 4 scans | `store_id SMALLINT, qty INT, unit_price DECIMAL(6,2)` | `df.writeTo(table).append()` |
| Store 9, 3 scans | `qty INT, unit_price DECIMAL(5,2)` | `df.write.mode("append").saveAsTable(table)` |

[`append_cast.py`](append_cast.py) runs as a one-off serverless job (environment version 6): it creates the Delta
table in a scratch schema with 3 seed scans from store 3, appends both batches as they are (no `cast()` anywhere),
then reads back the schema, the rows and `DESCRIBE HISTORY`.

**Must hold** (else the cloud run fails): both appends succeed, the table schema is unchanged (nothing widened,
narrowed or added), and the rows and per-store totals equal the pure-Python reference.

**Local contrast (in the test):** open-source Spark 4.3 on a parquet table: `saveAsTable` append casts too (store
assignment), but `writeTo()` refuses the table ("Cannot write into v1 table"), so store 7's batch never lands. On
Databricks before July, both writes into Delta failed with `DELTA_FAILED_TO_MERGE_FIELDS`.

## Why it matters
Store feeds rarely agree on integer widths and decimal precision. Before this release each feed needed a select
list of casts kept in sync with the table, or the job failed at 3 a.m. Now the writer does the safe casts and the
table schema stays the contract.

## How to run
```bash
python features/databricks/day-44-scan-batch-append-cast/append_cast.py   # reference, writes result.json
pytest -q tests/test_day44_databricks_append_cast.py                      # no credentials (local pyspark + fakes)
bash features/databricks/day-44-scan-batch-append-cast/cloud.sh           # GitHub Actions: DATABRICKS_HOST / TOKEN
```

## Cost
One short serverless job (two appends of 7 rows and a few reads): within Free Edition's serverless quota. Scratch
schema (with the table), volume and workspace folder are deleted on exit.

## Result
Reference: **10 scans, revenue $74.07**: store 3 (3 scans, 9 units, $13.21), store 7 (4 scans, 7 units, $34.33),
store 9 (3 scans, 7 units, $26.53); schema unchanged. Open-source Spark 4.3: store 9 appended and cast, store 7
refused by `writeTo()`. The cloud run is refused while the Free Edition account is inactive (see the report), so this
is verified locally only for now. Numbers in [`result.json`](result.json).
