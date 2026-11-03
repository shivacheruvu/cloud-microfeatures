# Day 29 · Google Cloud · The store price book as Apache Arrow, straight from `jobs.query`

**New platform feature:** **Arrow format query results** (Preview): set `queryResultsFormat: ARROW` on a
[`jobs.query`](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query) request and the response
carries the first page as an IPC-serialized Arrow schema and record batch (`arrowSchema.serializedSchema`,
`arrowRecordBatch.serializedRecordBatch`), optionally with `arrowSerializationOptions.bufferCompression` (`LZ4_FRAME`
or `ZSTD`). Announced in the [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) on
**2026-10-30** ([docs: Return query results in the Apache Arrow format](https://docs.cloud.google.com/bigquery/docs/running-queries#arrow-format)).
`jobs.getQueryResults` doesn't return Arrow, so larger results continue on the Storage Read API `_default` stream.

## What it does
[`price_book.py`](price_book.py) inlines a 120-row store price book (40 items x 3 stores: NUMERIC price, INT64 pack
size, BOOL promo flag, DATE, microsecond TIMESTAMP, and a computed `ROUND(price * 1000 / pack_g, 2)` price per kg) and
runs the same query three ways over REST:

1. default JSON rows (`{"f": [{"v": "4.49"}, ...]}`, every value a string; timestamps as int64 micros),
2. `queryResultsFormat: ARROW`,
3. `ARROW` with `bufferCompression: ZSTD`.

The Arrow batches are decoded with pyarrow (`ipc.read_schema` + `ipc.read_record_batch`) and must equal the hand-parsed
JSON rows and the local reference exactly: Decimal prices, ints, dates, timestamps to the microsecond.

## Result (local build of the same payloads)
| Encoding | Payload | Typed? |
|---|---|---|
| JSON rows (default) | 17,727 B | no: strings, parsed per schema type |
| Arrow | 10,824 B | yes: `decimal128(38, 9)`, `int64`, `bool`, `date32`, `timestamp[us, UTC]` |
| Arrow + ZSTD | 4,112 B | yes |

All 120 rows fit the first page; 10 are on promo; shelf value 552.81; dearest per kg is salmon at 32.32/kg.
The cloud run reports BigQuery's own byte counts and types next to these.

## Why it matters
A store app or notebook pulling the morning price book no longer has to choose between the slow-but-simple JSON
rows (hand parsing NUMERIC strings) and setting up a Storage Read API session: one REST call returns typed columnar
data that drops into pandas, Polars or DuckDB with no conversion code, at about a quarter of the JSON size with ZSTD.

## How to run
```bash
python features/gcp/day-29-price-book-arrow/price_book.py          # local: build + decode the payloads, result.json
python features/gcp/day-29-price-book-arrow/price_book.py --sql    # the BigQuery SQL
pytest -q tests/test_day29_gcp_arrow_results.py
bash features/gcp/day-29-price-book-arrow/cloud.sh                 # needs GCP_PROJECT_ID + gcloud auth (CI)
```

## Cost
Three queries over inlined literals: 0 bytes scanned (capped at 20 MiB billed each), so effectively free.
Nothing is created, so there's nothing to clean up.

## Limits of this slice
First page only (the documented path); results beyond it would need the Storage Read API. The feature is Preview, so
the response shape could still change; the run fails loudly if `arrowSchema` is missing.
