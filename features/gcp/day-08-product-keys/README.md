# Day 8 · Google Cloud · Product keys BigQuery generates: identity columns

**New platform feature:** [identity columns](https://docs.cloud.google.com/bigquery/docs/identity-columns)
(auto-incrementing `INT64` columns), **Preview, 2026-08-31**
([BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)). BigQuery fills the column on
insert. Values are unique, loosely ordered and sparse: not consecutive, but always a multiple of the increment apart.

## What it does
A product master whose surrogate key nobody has to compute:

```sql
CREATE TABLE ds.products (
  product_key INT64 GENERATED ALWAYS AS IDENTITY (START WITH 1000 INCREMENT BY 10),
  barcode STRING NOT NULL,
  name STRING
);
```

1. Load 8 products without a key. BigQuery generates one per row.
2. Add the weekly vendor feed (4 rows, 2 already known): only barcodes that aren't in the master get a key.
3. Try to insert a row with a hand-written `product_key`. `GENERATED ALWAYS` must refuse it.
4. Read `INFORMATION_SCHEMA.COLUMNS` (`is_identity`, `identity_generation`, `identity_start`,
   `identity_increment`).
5. Join 10 scan events (barcode, units) to the master, so the facts carry the generated key.

Locally, DuckDB does the same with a sequence (`START 1000 INCREMENT BY 10`) as the column default.

## Why it matters
Before this, a BigQuery product dimension needed `ROW_NUMBER()` + `MAX(key)` logic (which races under concurrent
loads) or `GENERATE_UUID()` strings. An identity column gives compact integer keys, and `ALWAYS` stops anyone
hand-keying a row.

## How to run
```bash
python features/gcp/day-08-product-keys/keys.py --local              # DuckDB; writes result.json
python features/gcp/day-08-product-keys/keys.py --sql create proj.ds # print one BigQuery statement
pytest -q tests/test_day08_gcp_identity_columns.py
```
`cloud.sh` runs in GitHub Actions (keyless) in a short-lived dataset (1-hour table expiration, deleted at the end).
BigQuery doesn't promise the exact key values, so the check compares properties: the same products as local,
no repeated key, every key ≥ 1000 and on the 10-step grid, the hand-written key refused, and the same units / scans
per product, each fact row carrying its product's key. The `INFORMATION_SCHEMA` values not reading
ALWAYS / 1000 / 10 is a warning.

## Cost
Seven statements over a few KB, each capped at 20 MiB billed: free tier. The dataset is deleted.

## Result (local)
| Product | Key | Units | Scans |
|---|---|---|---|
| whole milk 1gal | 1000 | 4 | 2 |
| large eggs 12ct | 1010 | 3 | 2 |
| sourdough loaf | 1020 | 1 | 1 |
| oat milk 64oz | 1040 | 1 | 1 |
| coffee beans 12oz | 1070 | 1 | 1 |
| ginger kombucha 16oz (new from feed) | 1080 | 6 | 2 |
| sparkling water 12pk (new from feed) | 1090 | 2 | 1 |

10 products, 8 + 2 new from the feed (sourdough and oat milk were already there). The cloud keys can differ from
these (sparse, loosely ordered); the cloud run reports them. Full numbers in [`result.json`](result.json).
