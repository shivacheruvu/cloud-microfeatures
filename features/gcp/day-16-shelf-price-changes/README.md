# Day 16 · Google Cloud · Shelf-price change log with `CHANGES`

**New platform feature:** the **`APPENDS` and `CHANGES` table functions** for BigQuery change history, **GA on
2026-07-27** ([BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)). This day uses
`CHANGES` with the `enable_change_history` table option. References:
[time series functions: CHANGES](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/time-series-functions#changes),
[work with change history](https://docs.cloud.google.com/bigquery/docs/change-history).

## What it does
A store's shelf-price table gets a week of edits, each its own commit:

| Commit | Edit |
|---|---|
| 1 | Monday load: 10 SKUs |
| 2 | Tuesday: supplier increase, dairy +20c (3 SKUs) |
| 3 | Wednesday: weekend markdown, P03 and P07 -50c |
| 4 | Thursday: P09 discontinued (DELETE) |
| 5 | Friday: new seasonal item P11 |

The table is created with `OPTIONS (enable_change_history = TRUE)`, and one query reads the whole history back:

```sql
SELECT DENSE_RANK() OVER (ORDER BY _CHANGE_TIMESTAMP) AS commit_no, sku, price_cents,
       _CHANGE_TYPE AS change_type, IFNULL(_CHANGE_IS_FOR_UPDATE, FALSE) AS is_for_update
FROM CHANGES(TABLE `<dataset>.shelf_prices`, NULL, NULL)
```

An `UPDATE` comes back as two rows: the old row as `DELETE` with `_CHANGE_IS_FOR_UPDATE = TRUE` and the new row as
`UPDATE`. The runner pairs them into old → new prices, and separates real deletes and new items.

## Why it matters
"What changed on the shelf this week, and from what to what?" normally needs an audit table, triggers or a CDC
pipeline. With change history on, the table keeps it for you (within its time-travel window), and `CHANGES`
returns it in SQL: price-change audits, shelf-tag reprints, syncing a POS or an external replica incrementally.

## How to run
```bash
python features/gcp/day-16-shelf-price-changes/price_changes.py --local           # Python replay; writes result.json
python features/gcp/day-16-shelf-price-changes/price_changes.py --sql setup P.DS  # CREATE + DML script
python features/gcp/day-16-shelf-price-changes/price_changes.py --sql changes P.DS
pytest -q tests/test_day16_gcp_changes.py
```
`cloud.sh` (GitHub Actions, keyless) creates a short-lived dataset, runs the setup as a script (separate commits, not
a transaction: `CHANGES` can't read a table with a multi-statement transaction in the window), runs the `CHANGES`
query, and fails unless every change row (commit, SKU, price, type, is-for-update) equals the local replay. The
dataset is deleted on exit; tables also expire after an hour.

## Cost
A few KB of data. Each DML statement bills the 10 MiB minimum (5 statements, ~50 MiB, capped at 100 MiB) and the
`CHANGES` query one table (capped at 20 MiB): well under $0.001 per run, inside the free tier. Change-history
metadata storage for a 11-row table that's deleted minutes later is negligible.

## Result
| SKU | Old → new | Commit |
|---|---|---|
| P01 Whole milk | 389 → 409 | 2 |
| P02 Greek yogurt | 549 → 569 | 2 |
| P03 Cheddar | 429 → 449 → **399** | 2, 3 |
| P07 Oat milk | 479 → 429 | 3 |

`CHANGES` returns **22 rows over 5 commits**: 11 inserts, **5 price updates** on 4 SKUs (each an old/new pair, so
10 rows), **P03 repriced twice** (net -30c), **P09** removed and **P11** added.

**Cloud run:** pending the post-merge workflow.

Full numbers in [`result.json`](result.json).
