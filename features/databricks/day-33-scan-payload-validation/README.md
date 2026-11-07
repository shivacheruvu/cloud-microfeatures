# Day 33 · Databricks · Quarantine malformed scan payloads with `is_valid_variant`

**New platform feature:** the **`is_valid_variant` SQL function**, added to serverless compute in the
[Databricks Runtime 19 updates of **2026-08-17**](https://docs.databricks.com/aws/en/release-notes/serverless):
*"returns `true` if the input is a well-formed `VARIANT` value and `false` otherwise"* (`NULL` for `NULL`). It needs
nothing but a serverless job, so it fits Databricks Free Edition; upstream it is in Apache Spark 4.2+, so the same SQL
runs on local pyspark 4.3.

## What it does
A lane-scanner exporter writes scan events to Parquet with a `VARIANT` column `payload`
(`{"lane", "sku", "qty", "price"}`), stored the standard way as two binaries (`metadata`, `value`). Readers apply the
contract `event_id STRING, payload VARIANT`. Three of the 14 rows come out broken the way a buggy writer breaks binary
variants:

| Event | Breakage |
|---|---|
| E05 | value bytes cut 3 bytes short (`truncated`) |
| E09 | metadata starts with an unknown version byte (`bad_header`) |
| E12 | value starts with a type byte the variant spec doesn't define (`bad_type`) |

[`scan_payloads.py`](scan_payloads.py) then:

1. extracts fields straight from the raw file (`variant_get(payload, '$.qty', 'int')` …): **the whole query fails
   with `MALFORMED_VARIANT`**, good rows included;
2. runs `SELECT event_id, is_valid_variant(payload)`: 11 `true`, 3 `false`;
3. writes only `WHERE is_valid_variant(payload)` rows to a clean folder; the `false` ones are the quarantine list;
4. builds the per-lane report from the clean folder, which must equal a pure-Python reference.

## Result (local reference; the cloud job must match it exactly)
| Lane | Scans | Units | Revenue |
|---|---|---|---|
| 1 | 4 | 6 | $23.24 |
| 2 | 4 | 9 | $21.41 |
| 3 | 3 | 7 | $22.83 |
| **total** | **11** | **22** | **$67.48** |

Quarantined: **E05, E09, E12** (14 events, 11 valid). Spot checks: `is_valid_variant(parse_json(...))` and
`is_valid_variant(parse_json('null'))` are `true`, `is_valid_variant(CAST(NULL AS VARIANT))` is `NULL`.

## Why it matters
`VARIANT` columns written by another tool are only as good as that tool's encoder. Without a check, one bad row makes
every extract over the file fail, and you can't even `WHERE` it away inside the same query: in local Spark 4.3, with the bad-header row present, a
`variant_get` in the same plan as the `is_valid_variant` filter still failed. Splitting into a validate-and-write step
and an extract step keeps the pipeline running and leaves the bad rows' IDs for the exporter's owner.

## How to run
```bash
python features/databricks/day-33-scan-payload-validation/scan_payloads.py   # local reference, writes result.json
pytest -q tests/test_day33_databricks_is_valid_variant.py                   # also runs it on local pyspark 4.3
bash features/databricks/day-33-scan-payload-validation/cloud.sh             # needs DATABRICKS_HOST/TOKEN (CI)
```

The cloud run (REST only, no SQL warehouse) uploads `scan_payloads.py`, submits it as a one-off serverless job on
environment version 6 with a scratch Unity Catalog volume folder as its work directory, compares the counts,
quarantine list, lane report and checks with `result.json`, and deletes the folder, the volume and the schema. If the
workspace refuses job runs (the Free Edition account has been inactive since 2026-10-24), it reports a warning and
verifies locally only.

## Cost
One short serverless job on Free Edition: no charge (within the daily quota).

## Limits of this slice
Three hand-made corruptions of an unshredded variant. Shredded Parquet variants (Spark 4.3 shreds by default on
write) and Delta tables with the `variantType` feature aren't covered.
