# Day 20 · Google Cloud · Substitute chains for out-of-stock items with BigQuery Graph

**New platform feature:** the GQL **path functions `IS_ACYCLIC`, `IS_SIMPLE`, `IS_TRAIL`** (and the `CALL` graph
statement), **generally available 2026-08-31** in the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes). References:
[GQL functions](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/graph-sql-functions),
[Create and query a graph](https://docs.cloud.google.com/bigquery/docs/graph-create),
[GQL patterns](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/graph-patterns). A property graph is
a logical view over ordinary tables, needs only BigQuery Data Editor on the dataset, and is billed as normal queries.

## What it does
Store staff keep a list of "if X is out, offer Y". Following it one hop isn't always enough (the first substitute is
often out too), and the list loops: the two oat milks point at each other, so do the two loaves. Two tables become a
graph:

```sql
CREATE PROPERTY GRAPH substitution_graph
  NODE TABLES (products KEY (sku) LABEL Product)
  EDGE TABLES (substitutes KEY (from_sku, to_sku)
    SOURCE KEY (from_sku) REFERENCES products (sku)
    DESTINATION KEY (to_sku) REFERENCES products (sku)
    LABEL SubstitutesFor);

GRAPH substitution_graph
MATCH p = (src:Product)-[s:SubstitutesFor]->{1,3}(dst:Product)
WHERE NOT src.in_stock
RETURN src.sku AS src_sku, dst.sku AS dst_sku, dst.in_stock AS dst_in_stock,
       PATH_LENGTH(p) AS hops, IS_ACYCLIC(p) AS acyclic, IS_TRAIL(p) AS trail;
```

One quantified pattern lists every 1-3 hop walk from an out-of-stock product; `IS_ACYCLIC` flags the walks that come
back to a product already visited, so they're never offered. The substitute is the in-stock product on the
fewest-hop acyclic walk (ties: closest price).

## Why it matters
Without the path functions you write a recursive CTE that carries the visited list and checks it by hand (that's the
local DuckDB twin in `graph.sql`). With the graph, "follow substitutes up to 3 hops, skip loops" is one `MATCH` and one
function call, and the same graph answers other questions later without new joins.

## How to run
```bash
python features/gcp/day-20-substitute-paths/substitutes.py --local            # DuckDB recursive CTE; writes result.json
python features/gcp/day-20-substitute-paths/substitutes.py --sql walks P.DS   # print the GQL query
pytest -q tests/test_day20_gcp_graph_paths.py
```
`cloud.sh` (GitHub Actions, keyless) creates a short-lived dataset, the two tables and the property graph, runs the
GQL query and fails if the acyclic walks or the substitute chosen for any product differ from the local model. If the
quantified pattern is refused, it re-reads the same walks with three fixed-length patterns and says so in a warning.
The dataset is deleted on exit.

## Cost
Two tiny `INSERT`s and one graph query (three in the fallback), each billed at the 10 MiB minimum and capped at
20 MiB: a fraction of a cent of free-trial credit. Nothing is left running.

## Result
| Out of stock | Offer | Hops |
|---|---|---|
| oat milk barista 64oz | cashew milk 64oz | 3 |
| oat milk original 64oz | almond milk 64oz | 2 |
| soy milk 64oz | almond milk 64oz | 1 |
| greek yogurt plain 32oz | icelandic yogurt 24oz | 2 |
| skyr plain 500g | icelandic yogurt 24oz | 1 |
| sourdough loaf | none within 3 hops | - |
| rustic white loaf | none within 3 hops | - |

**5 of 7** out-of-stock items get an in-stock substitute; of the **33** walks, `IS_ACYCLIC` drops **14** that loop
back (6 of them also reuse an edge, which `IS_TRAIL` flags). The bread pair only points at each other: the list
needs a new edge there.

Full numbers in [`result.json`](result.json).
