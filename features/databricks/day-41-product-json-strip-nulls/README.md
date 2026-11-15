# Day 41 · Databricks · Slim null-padded product payloads with variant_strip_nulls

**New platform feature:** **`variant_strip_nulls(v [, include_arrays])`** SQL / DataFrame function: recursively removes
object fields whose value is a variant null, and null array elements too unless `include_arrays` is `false`. Listed
in the [Databricks Runtime 19 release notes](https://docs.databricks.com/aws/en/release-notes/runtime/19) on
**2026-09-01** (serverless compute follows the Runtime 19 updates). Serverless only, no SQL warehouse, so it fits
Databricks Free Edition.

## What it does
An Open Food Facts-style exporter writes every known key for every product ([`product_payloads.py`](product_payloads.py),
8 synthetic products), so payloads are padded with JSON nulls: empty nutriments, a packaging block with nothing in it,
null placeholders in label lists. A serverless job (environment version 6):
1. `parse_json`s the payloads into a `VARIANT` column;
2. `variant_strip_nulls(payload)` removes the 28 nulls; `variant_strip_nulls(payload, false)` keeps null label slots;
3. reports per product: JSON bytes before / after, filled nutriments (`CAST(variant_get(..., '$.nutriments') AS
   MAP<STRING, VARIANT>)`), labels, label slots kept, and whether the packaging block is now `{}`;
4. checks every stripped payload equals a pure-Python strip of the same JSON, plus the documented edge cases
   (top-level `null` stays `null`, SQL `NULL` stays `NULL`).

Everything must equal the local reference ([`result.json`](result.json)); local pyspark 4.3 runs the same SQL in the
test.

## Why it matters
Null padding inflates storage and makes "is this field filled?" ambiguous (missing vs null). Stripping once at
ingest makes key presence mean "we have a value", so completeness is just a key count, and the payload is ~20% smaller.

## How to run
```bash
python features/databricks/day-41-product-json-strip-nulls/product_payloads.py   # writes result.json
pytest -q tests/test_day41_databricks_variant_strip_nulls.py                      # includes local pyspark 4.3
bash features/databricks/day-41-product-json-strip-nulls/cloud.sh                 # GitHub Actions (DATABRICKS_HOST / _TOKEN)
```

## Cost
One short serverless job run (well under a minute of serverless compute) on Free Edition: $0. The workspace folder,
scratch volume and schema are deleted on exit.

## Result (local)
| Product | Nutriments filled (of 5) | JSON bytes raw | JSON bytes stripped |
|---|---|---|---|
| 0001 Whole milk | 4 | 236 | 218 |
| 0002 Sourdough loaf | 4 | 205 | 136 |
| 0003 Bananas | 0 | 181 | 83 |
| 0004 Greek yogurt | 5 | 232 | 232 |
| 0005 Oat milk | 4 | 249 | 209 |
| 0006 Baby spinach | 2 | 213 | 176 |
| 0007 Salted butter | 4 | 200 | 126 |
| 0008 Bagels | 5 | 206 | 202 |
| **Total** | **70%** | **1,722** | **1,382 (-19.7%)** |

28 null values removed; with `include_arrays => false` the oat milk keeps all 4 label slots (2 null), with the default
it keeps 2. Cloud: the Free Edition workspace still refuses compute (see the day's report), so the job warns and this
is verified locally until it is back.
