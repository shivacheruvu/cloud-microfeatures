# Day 37 · Google Cloud · Brand units and flyer reach without double counting: BigQuery Graph measures

**New platform feature:** **BigQuery Graph measures** (GA): a node or edge table in a property graph can declare
`MEASURE(<aggregate>) AS name` properties whose aggregation is locked to the table's `KEY`. They are read only through
`GRAPH_EXPAND` (which flattens the graph to `<NodeTable>_<property>` columns) and `AGG(...)`, which aggregates once per
key before rolling up, so a fan-out join can't overcount. Announced in the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) on **2026-11-10**
([Work with measures](https://docs.cloud.google.com/bigquery/docs/graph-measures)). It runs on on-demand pricing
(graph queries allowed there since the 2026-11-06 release note, which day 33 used).

## What it does
A small promo graph ([`flyer_measures.py`](flyer_measures.py), synthetic): 3 brands, 8 products with weekly units,
4 flyers with household reach, and 14 **flyer slots** (a product placed on a flyer; oat milk is on three flyers).
`FlyerSlots → Products → Brands` and `FlyerSlots → Flyers` are edges; the measures are
`Products.total_units = MEASURE(SUM(weekly_units))`, `Products.product_count`, `Flyers.total_households` and
`FlyerSlots.slot_count`.

One `GRAPH_EXPAND` query groups by brand and puts the naive `SUM(Products_weekly_units)` /
`SUM(Flyers_households)` next to `AGG(Products_total_units)` / `AGG(Flyers_total_households)`. Every column must equal
the local model: DuckDB (aggregating at each key's grain) and plain Python, which the test requires to agree. A second
query, `SUM(Products_total_units)`, must be **refused**: measures can only be read with `AGG`.

## Why it matters
Promo analysts join products to flyers all the time, and the classic mistake is summing a product-level number over
placement-level rows. Here that inflates weekly units by **+89.5%** (Green Valley's oat milk on three flyers makes
its brand look 2.19x bigger) and flyer reach by 30-76%. With measures the grain lives in the graph definition, so
every query (or an agent writing one) gets the right number, and the wrong one is a type error.

## How to run
```bash
python features/gcp/day-37-flyer-reach-measures/flyer_measures.py   # DuckDB + plain Python, writes result.json
pytest -q tests/test_day37_gcp_graph_measures.py
GCP_PROJECT_ID=... bash features/gcp/day-37-flyer-reach-measures/cloud.sh   # in GitHub Actions (keyless)
```

## Cost
A temporary dataset (deleted on exit, tables expire in 1 h), four tiny INSERTs and two queries, each billed at the
10 MiB minimum with a 20 MiB cap: well under one cent on on-demand pricing. No reservation or edition is bought.

## Result (local)
| Brand | Products | Slots | Units: SUM (naive) | Units: AGG (measure) | Households: SUM | Households: AGG |
|---|---|---|---|---|---|---|
| Green Valley Dairy-Free | 3 | 6 | 1,820 | **830** | 117,000 | **78,000** |
| Harbor Bakery | 2 | 3 | 670 | **490** | 111,000 | **63,000** |
| Sunny Fields Produce | 3 | 5 | 2,190 | **1,150** | 114,000 | **84,000** |
| **Total** | 8 | 14 | 4,680 | **2,470** | | |

Households AGG = the sum over the distinct flyers carrying the brand (an upper bound on reach: flyer audiences may
overlap). Numbers in [`result.json`](result.json).

**Cloud:** runs in GitHub Actions after merge; see the report for the outcome.
