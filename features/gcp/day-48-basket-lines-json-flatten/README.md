# Day 48 · Google Cloud · Basket lines from nested order JSON with JSON_FLATTEN

**New platform feature:** **`JSON_FLATTEN`** (GA per the BigQuery release notes, **2026-11-11**): a GoogleSQL JSON
function that returns, as an `ARRAY<JSON>`, every non-array value sitting directly in the input or inside
consecutively nested arrays. From the [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)
(November 11, 2026) ([reference: JSON_FLATTEN](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/json_functions#json_flatten)).
The reference page still carried a Pre-GA banner on 2026-11-21; the release note says GA.

## What it does
A grocery app sends each order's `lines` as JSON in whatever shape the cart had ([`data/orders.jsonl`](data/orders.jsonl)):
a flat list, a meal deal grouped as a nested array, a bundle inside a bundle (three levels), a single line sent as a
bare object, a line that carries its own nested `subs` array, empty bundles, and an abandoned cart with no lines
(SQL NULL). One query flattens and prices them all:

```sql
SELECT order_id,
       TO_JSON_STRING(JSON_FLATTEN(lines)) AS flat,
       (SELECT SUM(LAX_INT64(l.qty) * LAX_INT64(l.cents)) FROM UNNEST(JSON_FLATTEN(lines)) AS l) AS total_cents,
       (SELECT STRING_AGG(LAX_STRING(l.sku), ',' ORDER BY pos)
          FROM UNNEST(JSON_FLATTEN(lines)) AS l WITH OFFSET AS pos) AS skus
FROM orders
```

[`basket.py`](basket.py) holds a pure-Python `JSON_FLATTEN` (checked against the five examples on the reference page),
the SQL and the check. Orders are inlined: nothing is created.

**Must hold** (else the cloud run fails): 8 rows; per order the flattened array equals the reference element for
element and in order (the `subs` arrays stay inside their line, the bare object is wrapped, empty bundles give `[]`,
NULL stays NULL), and line count, units, basket total and SKU order match.

## Why it matters
Nested order payloads are normal (bundles, meal deals, multipacks), and the nesting depth isn't fixed. Before this,
getting every line out took a recursive JavaScript UDF or a fixed stack of `UNNEST`s that silently drops a deeper
level. `JSON_FLATTEN` makes it one call, and leaves arrays inside objects alone, so a line's own attributes survive.

## How to run
```bash
python features/gcp/day-48-basket-lines-json-flatten/basket.py         # reference, writes result.json
python features/gcp/day-48-basket-lines-json-flatten/basket.py --sql   # the BigQuery SQL
pytest -q tests/test_day48_gcp_json_flatten.py                          # no credentials needed
bash features/gcp/day-48-basket-lines-json-flatten/cloud.sh            # GitHub Actions (keyless)
```

## Cost
One query over inlined literals: 0 bytes scanned, no model calls, $0. Nothing to clean up.

## Result
Reference (local): 8 orders, 17 line objects, 28 units, basket total 4,188 cents; 5 orders nested beyond one level
(deepest: 3), 1 NULL order. Per-order figures in [`result.json`](result.json). The cloud run must match every order.

**Cloud run (2026-11-21, first run after the merge):** passed on the first try. `JSON_FLATTEN` returned exactly the
reference for all 8 orders, element for element and in order: 17 line objects, 28 units, 4,188 cents; the three-level
bundle came out flat, the bare object was wrapped, the `subs` arrays stayed inside their line, `[[], [[]]]` gave `[]`
and the NULL order stayed NULL. 0 bytes scanned.
