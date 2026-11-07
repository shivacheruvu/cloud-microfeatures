# Day 33 · Google Cloud · Route stock transfers with BigQuery Graph `ANY SHORTEST`, on on-demand pricing

**New platform feature:** **BigQuery Graph and GQL queries on on-demand pricing**, from the
[Google Cloud release notes, **2026-11-06**](https://docs.cloud.google.com/release-notes): *"You can now use BigQuery
Graph and run Graph Query Language (GQL) queries when you use on-demand pricing."* Until then a GQL query needed an
Enterprise or Enterprise Plus reservation, which is why [day 20](../day-20-substitute-paths/) could only create its
graph in the cloud and verify the walks locally. This day uses the
[`ANY SHORTEST` path search prefix](https://docs.cloud.google.com/bigquery/docs/graph-query-overview) and a `LET`
horizontal aggregation over the path's edges.

## What it does
Nine stores, nine two-way roads with drive minutes. Two stores have surplus oat-milk cases (the DC with 60, Riverside
with 24), five are short of this week's target. [`transfer_routes.py`](transfer_routes.py) builds a property graph
(`Store` nodes, `Road` edges) and runs one GQL query:

```sql
GRAPH `<dataset>`.store_routes
MATCH ANY SHORTEST (src:Store WHERE src.surplus_cases > 0)-[e:Road]->{1, 3}(dst:Store WHERE dst.short_cases > 0)
LET minutes = SUM(e.minutes)
RETURN src.store_id AS src, dst.store_id AS dst, ARRAY_LENGTH(e) AS hops, minutes
ORDER BY src, dst;
```

`ANY SHORTEST` returns one fewest-hop path per (source, destination) pair. Each short store is then served from the
source with the fewest hops (ties: fewer minutes). The road network is built so every fewest-hop path is unique
(the local model refuses ambiguous ones), so `ANY SHORTEST`'s pick, and its minutes, are deterministic.

## Result (local model; the cloud query must match it exactly)
7 surplus-to-short pairs are within 3 hops:

| Short store | Cases | From | Hops | Drive minutes |
|---|---|---|---|---|
| S3 Oak Park | 6 | DC | 2 | 22 |
| S5 Hillcrest | 10 | S2 Riverside | 2 | 16 (DC: 3 hops, 34 min) |
| S6 Lakeview | 4 | S2 Riverside | 2 | 20 (DC: 3 hops, 38 min) |
| S7 North Gate | 5 | S2 Riverside | 3 | 33 (DC is 4 hops: out of reach) |
| S8 Airport | 3 | none | | no source within 3 hops |

**4 of 5 short stores routed, 25 cases, 91 drive minutes**; S8 needs a 4th hop or a new source.
The local model is a DuckDB recursive CTE that must equal a plain-Python BFS.

## Why it matters
Graph questions (nearest source, reachability within N hops) are a few lines of GQL instead of recursive SQL, and
with on-demand pricing a free-trial project pays only for bytes scanned, not for a reservation slot. The same change
also lets day 20's daily re-run reach its GQL query.

## How to run
```bash
python features/gcp/day-33-stock-transfer-routes/transfer_routes.py     # local model, writes result.json
pytest -q tests/test_day33_gcp_graph_on_demand.py
bash features/gcp/day-33-stock-transfer-routes/cloud.sh                  # needs GCP_PROJECT_ID (CI, keyless)
```

`cloud.sh` creates a short-lived US dataset (tables expire in an hour), the two tables and the graph, runs the GQL
query on on-demand pricing and compares rows and plan with the local model; the dataset is deleted on exit. If the
query is still refused for want of a reservation (the change not yet reached the project), it warns and stops.

## Cost
Two tiny `INSERT`s and one graph query, each billed at the 10 MiB minimum and capped at 20 MiB: well under a cent.

## Limits of this slice
Routes, not an allocation: source capacity isn't split between stores (Riverside's 24 cases cover the 19 it's asked
for here). `ANY SHORTEST` is by hop count, not drive minutes.
