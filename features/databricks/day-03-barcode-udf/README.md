# Day 3 · Databricks · Barcode audit with a Unity Catalog Python UDF

**New platform feature:** [Scalar and Batch Unity Catalog Python UDFs, generally available](https://docs.databricks.com/aws/en/udf/unity-catalog),
released **2026-09-23** ([release notes](https://docs.databricks.com/aws/en/release-notes/product/2026/september)).
The GA release adds **named handlers** for scalar UDFs (on serverless SQL warehouses they need
`ENVIRONMENT (environment_version = '6')` or above) and removes the five-UDF-calls-per-query limit.

## What it does
A store day of checkout scans (240 scans, 4 lanes) goes through `gtin_status(barcode)`, a governed Python UDF that
applies the GS1 check-digit rule to GTIN-8 / UPC-A / EAN-13 / GTIN-14 codes and returns `valid`,
`bad_check_digit`, `bad_length` or `not_numeric`. The audit query counts each status per lane.

```sql
CREATE OR REPLACE FUNCTION microfeatures_day03.gtin_status(code STRING)
RETURNS STRING LANGUAGE PYTHON
HANDLER 'gtin_status_handler'
ENVIRONMENT (environment_version = '6')
AS $$ ... def gtin_status_handler(code): ... $$
```

## Why it matters
Barcode typos (manual entry, a swapped digit, a dropped digit) look like real products and quietly create
"unknown item" rows downstream. A check digit catches every single-digit error. Writing it once as a Unity Catalog
function means every SQL user and dashboard gets the same rule, with permissions, instead of copying a regex. The
named handler keeps setup code (constants, helpers) outside the per-row function.

## How to run
```bash
python barcode_audit.py --local        # DuckDB runs the UDF's own Python body as a DuckDB function; writes result.json
python barcode_audit.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN; serverless SQL warehouse
```
In CI, `cloud.sh` creates the schema `microfeatures_day03` and the UDF, runs the audit through the SQL Statement
Execution API, fails unless the result is identical to the local one, then drops the UDF and schema.
The test (`tests/test_day03_databricks_barcode_udf.py`) checks the UDF body against an independent GS1
implementation, against known codes, and that every single-digit error in a code is caught.

## Cost
Four small statements on a serverless SQL warehouse (seconds of compute plus idle time until auto-stop), covered by
the 14-day trial credits. Python UDFs on serverless SQL warehouses also work on Databricks Free Edition.

## Result (synthetic, seeded store day)
| Lane | Scans | Valid | Bad check digit | Bad length | Not numeric | Invalid % |
|---|---|---|---|---|---|---|
| lane-1 | 75 | 73 | 0 | 0 | 2 | 2.7 |
| lane-2 | 52 | 49 | 2 | 0 | 1 | 5.8 |
| lane-3 | 58 | 57 | 0 | 1 | 0 | 1.7 |
| self-checkout | 55 | 49 | 2 | 2 | 2 | 10.9 |

12 of 240 scans (5.0%) can't be real GTINs; self-checkout has the worst rate (10.9%). A dropped digit from an
EAN-13 can still be 12 digits long, so it shows up as a bad check digit rather than a bad length.
