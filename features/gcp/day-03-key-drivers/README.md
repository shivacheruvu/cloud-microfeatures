# Day 3 · Google Cloud · What moved this week's grocery sales? `AI.KEY_DRIVERS`

**New platform feature:** [`AI.KEY_DRIVERS` function](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-key-drivers),
generally available **2026-09-29** ([BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)).

## What it does
Units sold per store × category × channel for two weeks (3 stores, 5 categories, in-store / online). Total units
rose only **+2.5%** (11,516 → 11,799), which hides two big moves. One `AI.KEY_DRIVERS` call compares this week
(interest) with last week (reference) across **every segment** (72 here, from `[all]` down to
store & category & channel) and returns each segment's sums, difference, unexpected difference, support and
contribution.

```sql
SELECT drivers, metric_interest, metric_reference, difference, unexpected_difference, apriori_support, contribution
FROM AI.KEY_DRIVERS((SELECT store, category, channel, units, week = 'this_week' AS is_this_week FROM ...),
  metric_col => 'units', dimension_cols => ['store', 'category', 'channel'],
  interest_label_col => 'is_this_week', min_apriori_support => 0, enable_pruning => FALSE)
```

## Why it matters
"Sales are up 2.5%" is the number everyone sees; the drivers are what someone needs to act on. Before this, the
same answer took a `GROUP BY CUBE`, two filtered sums, a self-join to totals and hand-written ranking, or a
contribution-analysis model (`CREATE MODEL` + `ML.GET_INSIGHTS`). `AI.KEY_DRIVERS` is one table function, needs no
model and no Gemini call, and is billed as a normal query.

## How to run
```bash
python key_drivers.py --local          # DuckDB GROUP BY CUBE reference for every segment; writes result.json
python key_drivers.py --sql            # the BigQuery query, data inlined (nothing to load or delete)
```
In CI, `cloud.sh` runs the query with `bq` and fails unless every segment's interest, reference, difference,
support and contribution equal the local values. `unexpected_difference` is compared with a local formula
(interest minus reference × complement's growth; for `[all]`, the whole difference) and only warns if BigQuery
defines it differently. The first cloud run (2026-10-07) matched all 72 segments, and the formula on 71; `[all]`
was aligned to BigQuery afterwards.
The test (`tests/test_day03_gcp_key_drivers.py`) checks the local reference against a plain-Python segment walk.

## Cost
One query over a few KB of inlined data, capped at 20 MiB billed: inside the BigQuery free tier. No dataset or
table is created, so there is nothing to clean up.

## Result (synthetic, seeded)
| Segment | Last week | This week | Difference | Apriori support |
|---|---|---|---|---|
| all | 11,516 | 11,799 | +283 | 1.00 |
| category=bakery | 2,581 | 3,068 | +487 | 0.26 |
| category=produce & store=north | 956 | 526 | **−430** | 0.08 |
| category=produce & channel=in_store & store=north | 834 | 459 | −375 | 0.07 |
| category=bakery & channel=online | 460 | 828 | **+368** | 0.07 |
| category=produce | 2,603 | 2,236 | −367 | 0.23 |

The two planted events come out on top among two-dimension segments: north store's produce fell 45% (a broken
cooler, say) and online bakery orders rose 80% across all stores, while the overall number moved only 2.5%.
