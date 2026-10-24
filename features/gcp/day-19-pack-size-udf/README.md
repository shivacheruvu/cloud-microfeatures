# Day 19 · Google Cloud · Shelf unit prices from free-text pack sizes (Python UDF)

**New platform feature:** **Python user-defined functions in BigQuery**, **generally available 2026-05-20**:
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) (Python UDFs GA; real-time
logging for them followed on 2026-08-26). Reference:
[Work with user-defined functions in Python](https://docs.cloud.google.com/bigquery/docs/user-defined-functions-python).
`CREATE FUNCTION ... LANGUAGE python OPTIONS (runtime_version = "python-3.11", entry_point = "...") AS r'''...'''`;
persistent functions only (no `TEMP`), no connection needed unless the code calls out, `STRUCT` maps to a Python
`dict`; `JSON`, `RANGE`, `INTERVAL` and `GEOGRAPHY` aren't supported. The first call after `CREATE FUNCTION` waits for
BigQuery to build the function's container.

## What it does
Shelf labels write pack sizes every way: `1 gal`, `64 fl oz`, `2 x 1 l`, `12pk 12 fl oz`, `1.5 lb`, `2x200g`, `12ct`.
`parse_pack.py` (standard-library Python, about 35 lines) turns a label into `STRUCT<base_qty INT64, unit STRING>` in
grams, millilitres or items, and `unit_prices.sql` ranks every category by price per 100 g / 100 ml / item:

```sql
WITH parsed AS (SELECT category, sku, label, price_cents, `p.ds`.parse_pack(label) AS pack FROM `p.ds`.pack_items)
... ROW_NUMBER() OVER (PARTITION BY category, unit ORDER BY price per unit, sku) AS value_rank
```

The same file is the UDF body in BigQuery and the function the local run imports, so the parser has one source.

## Why it matters
Doing this in SQL means nested `REGEXP_EXTRACT`s and a CASE per unit; a JavaScript UDF works but grocery analysts
already write Python. With Python UDFs the parser is ordinary, unit-tested Python, called per row from SQL, and the
"which pack is cheaper" answer lives next to the data instead of in a notebook.

## How to run
```bash
python features/gcp/day-19-pack-size-udf/pack_prices.py --local            # DuckDB + the same Python function; result.json
python features/gcp/day-19-pack-size-udf/pack_prices.py --sql udf P.DS      # print the CREATE FUNCTION
pytest -q tests/test_day19_gcp_python_udf.py
```
`cloud.sh` (GitHub Actions, keyless) creates a dataset in `US`, the Python UDF, the 21-item table, runs the query and
fails unless BigQuery's rows equal the local model; the dataset (table and function) is deleted on exit. Locally,
DuckDB runs `unit_prices.sql` with `parse_pack` registered as a Python function and must equal a plain-Python model.

## Cost
A CTAS and one query over 21 rows, each billed at the 10 MiB minimum (capped at 20 MiB): about $0.0001. The Python
UDF adds a few seconds of managed compute for 21 calls; well under a cent. Nothing is left running.

## Result
| Category | Best value | Pack | Price per |
|---|---|---|---|
| chips | tortilla chips 13oz | 369 g | 121.7c / 100 g |
| coffee | ground coffee 1.5 lb | 680 g | 220.4c / 100 g |
| eggs | large eggs 18 count | 18 ct | 33.3c / item |
| milk | whole milk 1 gal | 3785 ml | 10.3c / 100 ml |
| rice | basmati rice 5 kg | 5000 g | 38.0c / 100 g |
| water | sparkling water 1.5L | 1500 ml | 8.6c / 100 ml |
| yogurt | greek yogurt 32oz | 907 g | 60.5c / 100 g |

20 of 21 labels parsed; `tortilla chips family size` has no size and gets NULLs and no rank. Same-size different
spelling shows up too: `ground coffee 12oz` and `ground coffee 340 g` are both 340 g, but the 12oz bag is 2.9c/100 g
cheaper.

**Cloud run (2026-10-23, post-merge of #73):** BigQuery created the Python UDF with the CI service account, the first
query (container build included) took 16 s, and all 21 rows matched the local model.
