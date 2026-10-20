# Day 15 · Google Cloud · One freshness check for every feed, with an ANY TABLE table function

**New platform feature:** **table parameters of type `ANY TABLE` in table-valued functions** (BigQuery, **GA
2026-08-11**, [release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)). Per the
[table functions page](https://docs.cloud.google.com/bigquery/docs/table-functions): "Using the `ANY TABLE` type in a
TVF lets you create generic functions that accept tables of any structure", as long as the table "contain[s] the
specific columns used inside the function".

## What it does
Three grocery feeds land in the warehouse with different columns but the same two timestamps:

| Feed | Columns | SLA |
|---|---|---|
| scans | store, lane, sku, qty, event_ts, loaded_ts | 15 min |
| deliveries | store, supplier, cases, event_ts, loaded_ts | 120 min |
| price_tags | store, sku, price_cents, event_ts, loaded_ts | 30 min |

One table function, written once, checks all three: rows per store, rows that landed later than the SLA, and the
worst delay. It's a single multi-statement query in a short-lived dataset that `cloud.sh` deletes afterwards:

```sql
CREATE OR REPLACE TABLE ds.scans AS SELECT * FROM UNNEST([...]);        -- and deliveries, price_tags
CREATE OR REPLACE TABLE FUNCTION ds.late_rows(input_table ANY TABLE, feed_name STRING, sla_minutes INT64) AS (
  SELECT feed_name AS feed, store, COUNT(*) AS n_rows,
         COUNTIF(TIMESTAMP_DIFF(loaded_ts, event_ts, MINUTE) > sla_minutes) AS late_rows,
         MAX(TIMESTAMP_DIFF(loaded_ts, event_ts, MINUTE)) AS max_delay_min
  FROM input_table GROUP BY store);
SELECT * FROM ds.late_rows(TABLE ds.scans, 'scans', 15)
UNION ALL SELECT * FROM ds.late_rows(TABLE ds.deliveries, 'deliveries', 120)
UNION ALL SELECT * FROM ds.late_rows(TABLE ds.price_tags, 'price_tags', 30);
```
(The first version used temporary tables and `CREATE TEMP TABLE FUNCTION`, as in an example on the table functions
page. BigQuery answered "CREATE TEMPORARY TABLE FUNCTION statements are not currently supported", so it was fixed
forward on 2026-10-19 with a dataset that lives for the run.)
Before `ANY TABLE`, a table parameter had to spell out its schema (`TABLE<store STRING, ...>`), so a generic check like
this needed a copy per feed or a view per feed to project the common columns. Locally, plain Python computes the same
rows, and the test cross-checks them with a DuckDB table macro (`query_table`) over the same three tables.

## Why it matters
Freshness, row-count and null checks are the same on every feed; only the table changes. `ANY TABLE` lets one
function hold the rule, and each new feed just has to carry the columns it needs.

## How to run
```bash
python features/gcp/day-15-feed-freshness/freshness.py --local   # plain Python; writes result.json
python features/gcp/day-15-feed-freshness/freshness.py --sql     # print the BigQuery query (data inlined)
pytest -q tests/test_day15_gcp_any_table_tvf.py
```
`cloud.sh` creates the dataset, runs the query with `bq` in GitHub Actions (keyless), deletes the dataset, and fails on any missing, extra or different
(feed, store) row.

## Cost
One script over ~20 KB of inlined data. BigQuery bills at least 10 MiB per table read, and the final `SELECT` reads
3 tables, so the run is billed ~30 MiB and capped at 50 MiB (well inside the 1 TiB/month free tier), plus a few KB of storage
for minutes: the dataset is deleted on exit and its tables expire after an hour anyway.

## Result
**14 of 129** rows landed late, in 4 feed/store pairs:

| Feed / store | Late / rows | Worst delay |
|---|---|---|
| deliveries / S3 | 5 / 5 | 521 min (the supplier portal batches overnight) |
| scans / S2 | 6 / 30 | 92 min (the lane uploader stalls over lunch) |
| price_tags / S2 | 2 / 8 | 38 min |
| price_tags / S3 | 1 / 8 | 31 min |

Store S1 is inside every SLA.

**Cloud run (2026-10-19):** after three fixes forward (no temporary table functions in BigQuery yet, the 10 MiB minimum
per table read, and the nested array `bq` prints for a script), the `ANY TABLE` function ran on all three tables in
the post-merge workflow and matched local for all 9 (feed, store) rows. Stores and events are made up. Full numbers in [`result.json`](result.json).
