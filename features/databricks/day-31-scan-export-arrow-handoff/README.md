# Day 31 · Databricks · Register scan export into Spark as Arrow, NULLs intact

**New platform feature:** **`createDataFrame()` handles pandas DataFrames with PyArrow `ChunkedArray` columns**
(PySpark), listed in the [Databricks serverless compute release notes](https://docs.databricks.com/aws/en/release-notes/serverless)
under the **2026-08-17** Databricks Runtime 19 updates ("preventing type errors with Arrow-backed pandas data"). It runs
on serverless jobs, so it fits Databricks Free Edition; upstream it is in pyspark 4.3, so the same call runs locally.

## What it does
[`arrow_handoff.py`](arrow_handoff.py) builds a register scan export the way lanes write it: one Arrow record batch per
lane (lane-1, lane-2, self-checkout), stored as a Parquet file with one row group each, 42 scans in all. It reads the
file back as Arrow-backed pandas and hands it to Spark unchanged:

```python
pdf = pq.read_table(buf).to_pandas(types_mapper=pd.ArrowDtype)   # every column: a 3-chunk pa.ChunkedArray
df = spark.createDataFrame(pdf)                                   # the new part: no astype / to_numpy first
```

Then one Spark SQL query gives the lane report: scans, units, revenue, unpriced scans (price-lookup misses, NULL
price), loyalty scans, distinct SKUs.

## Result (local reference; the cloud job must match it exactly)
Spark schema: `lane:string, sku:string, product:string, qty:bigint, unit_price:double, loyalty_id:string`

| Lane | Scans | Units | Revenue | Unpriced scans | Loyalty scans | SKUs |
|---|---|---|---|---|---|---|
| lane-1 | 14 | 35 | $123.31 | 2 | 9 | 11 |
| lane-2 | 11 | 27 | $110.35 | 2 | 8 | 11 |
| self-checkout | 17 | 43 | $132.78 | 3 | 11 | 11 |
| **total** | **42** | 105 | **$366.44** | **7** | 28 (66.7%) | |

The 7 unpriced scans are the two items without a shelf price (seasonal squash, bulk almonds). They arrive in Spark as
real NULLs, so `COUNT_IF(unit_price IS NULL)` finds all 7 and revenue skips them instead of turning into NaN. The 14
anonymous shoppers stay NULL in `loyalty_id` too.

**Finding:** on local pyspark 4.3 the same DataFrame only goes in through the Arrow path. With
`spark.sql.execution.arrow.pyspark.enabled=false` (row-by-row conversion), `createDataFrame` fails with
`CANNOT_MERGE_TYPE` on the nullable Arrow string column. Serverless (Spark Connect) always uses Arrow, which is where
this release applies.

## Why it matters
pandas 2+ readers (`read_parquet(dtype_backend="pyarrow")`, `pyarrow.Table.to_pandas(types_mapper=pd.ArrowDtype)`)
hand you Arrow-backed columns made of chunks. Before this release, passing such a DataFrame to `createDataFrame` on
serverless raised type errors, so code had to convert columns to NumPy first, which turns missing integers into floats
and missing prices into NaN. Now the export goes in as it is, with types and NULLs kept.

## How to run
```bash
python features/databricks/day-31-scan-export-arrow-handoff/arrow_handoff.py    # local reference, writes result.json
pytest -q tests/test_day31_databricks_arrow_handoff.py                         # also runs the real call on local pyspark
bash features/databricks/day-31-scan-export-arrow-handoff/cloud.sh             # needs DATABRICKS_HOST/TOKEN (CI)
```

The cloud run (REST only, no SQL warehouse) uploads `arrow_handoff.py` to a workspace folder, submits it as a one-off
serverless job on **environment version 6**, reads the result from a scratch volume, compares the schema, chunk
counts, NULL counts and every lane total with `result.json`, and deletes the folder, volume and schema. If the
workspace refuses job runs (the Free Edition account has been inactive since 2026-10-24), it reports a warning and
verifies locally only.

## Cost
One short serverless job run on Free Edition: no charge (within the daily quota).

## Limits of this slice
Small synthetic data on purpose. Only string, int64 and double Arrow types are covered; timestamps with time zones,
decimals and nested Arrow types are not tested here.
