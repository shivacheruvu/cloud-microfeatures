# Day 38 · Google Cloud · Count every picked item in nested tote manifests: JSON_FLATTEN in BigQuery

**New platform feature:** **`JSON_FLATTEN`** (GA): returns an `ARRAY<JSON>` of every non-array value that is directly in
a JSON value or nested inside any number of consecutive arrays. Objects are kept whole, so arrays *inside* an object
stay put. Announced in the [Google Cloud release notes](https://docs.cloud.google.com/release-notes) (BigQuery) on
**2026-11-11** ([reference: JSON functions](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/json_functions#json_flatten)).

## What it does
Eight synthetic online grocery orders ([`data/orders.jsonl`](data/orders.jsonl), from [`make_data.py`](make_data.py))
come from a picker app whose `totes` value nests as deep as the picker happened to pack:

| Packing | Depth of the item | Orders |
|---|---|---|
| tote → bag → item (the usual case) | 3 | all but W-1006 |
| item dropped straight into a tote | 2 | W-1004, W-1007 |
| produce bag inside a bag | 4 | W-1005, W-1007 |
| express order: `totes` is a flat item list | 1 | W-1006 |
| cancelled before picking: no `totes` | – | W-1008 |

Some items carry a `subs` array of approved substitutes.

One query ([`tote_manifest.sql`](tote_manifest.sql), orders inlined) unnests
`JSON_FLATTEN(JSON_QUERY(doc, '$.totes'))` and totals items, units and value per order. Next to it, the pre-JSON_FLATTEN
way: a fixed-depth `JSON_QUERY_ARRAY` unnest (tote → bag → item). Every number per order must equal the plain-Python
model in [`tote_manifest.py`](tote_manifest.py). A second query ([`edge_cases.sql`](edge_cases.sql)) runs the five
documented examples plus empty arrays, JSON `null` and SQL `NULL`, and reports them next to the local model.

## Why it matters
Semi-structured exports rarely have a fixed depth. A fixed-depth unnest doesn't fail when the shape changes: it **drops
rows without an error**. Here it misses 8 of 33 items and $108.96 of $418.62, all from the four orders packed in
an unusual way. `JSON_FLATTEN` doesn't depend on the depth, and because it stops at objects, an item's `subs` array is
not counted as extra items.

## How to run
```bash
python features/gcp/day-38-tote-manifest-flatten/tote_manifest.py      # local model, writes result.json
pytest -q tests/test_day38_gcp_json_flatten.py
GCP_PROJECT_ID=... bash features/gcp/day-38-tote-manifest-flatten/cloud.sh   # in GitHub Actions (keyless)
```

## Cost
Two queries over inlined literals: 0 bytes scanned, so $0 under on-demand pricing (`--maximum_bytes_billed` 20 MiB
cap). Nothing is created.

## Result (local model)
| Order | Items (JSON_FLATTEN) | Items (fixed depth) |
|---|---|---|
| W-1001 | 9 | 9 |
| W-1002 | 4 | 4 |
| W-1003 | 4 | 4 |
| W-1004 | 4 | **3** |
| W-1005 | 4 | **2** |
| W-1006 | 3 | **0** |
| W-1007 | 5 | **3** |
| W-1008 (cancelled) | 0 | 0 |
| **Total** | **33 items, 68 units, $418.62** | **25 items, 54 units, $309.66** |

Items by depth: 3 at depth 1, 2 at depth 2, 25 at depth 3, 3 at depth 4. Six items with substitutes are counted once.
Numbers in [`result.json`](result.json).

**Cloud (2026-11-11, after #136 merged): ran and matched.** All 8 orders equal the local model in BigQuery: 33 items /
68 units / $418.62 with `JSON_FLATTEN`, 25 items / $309.66 with the fixed-depth unnest; item order equals document
order on 8/8 orders. The edge-case query matched the local model on 9/9: `[]` and `[[], [[]], [1]]` flatten to `[]` and
`[1]` (empty arrays contribute nothing), JSON `null` gives `[null]`, SQL `NULL` gives SQL `NULL`.
