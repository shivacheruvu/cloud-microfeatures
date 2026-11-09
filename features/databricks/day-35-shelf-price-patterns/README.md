# Day 35 · Databricks · Temporary price reductions and shopping trips with MATCH_RECOGNIZE

**New platform feature:** the **`MATCH_RECOGNIZE` clause** (row pattern recognition, **Beta**), in the
[Databricks SQL release notes 2026](https://docs.databricks.com/aws/en/sql/release-notes/2026) on **2026-06-30**;
applies to Databricks SQL and Databricks Runtime 19+
([reference](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-match-recognize),
[PATTERN](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-match-recognize-pattern)).
Runs on serverless, so it fits Databricks Free Edition.

## What it does
Two row-pattern queries over grocery data, in [`price_patterns.py`](price_patterns.py):

**1. Temporary price reductions (TPRs) in daily shelf prices.** 6 SKUs × 21 days (2026-10-12 → 11-01):
```sql
MATCH_RECOGNIZE (
  PARTITION BY sku ORDER BY day
  MEASURES MATCH_NUMBER() AS dip_no, FIRST(day) AS start_day, FIRST(down.day) AS cut_day, LAST(day) AS back_day,
           COUNT(*) AS days, FIRST(price) AS before_cents, LAST(down.price) AS promo_cents, LAST(up.price) AS back_cents
  ONE ROW PER MATCH AFTER MATCH SKIP PAST LAST ROW
  PATTERN ( strt down+ (flat | down)* up+ )
  DEFINE down AS price < PREV(price), flat AS price = PREV(price), up AS price > PREV(price) )
```
A cut (possibly in steps), held, then raised again. Price rises only (olive oil), flat (bananas) and a permanent cut
(ice cream) are correctly *not* TPRs.

**2. Shopping trips from loyalty scans.** 84 scans on 8 cards over a weekend; a new trip starts when more than 45
minutes pass: `PATTERN ( strt same_trip* )` with `DEFINE same_trip AS scan_ts <= PREV(scan_ts) + INTERVAL 45 MINUTE`.

**Local engine.** `MATCH_RECOGNIZE` isn't in open-source Spark (pyspark 4.3 gives `PARSE_SYNTAX_ERROR`), so the local
reference applies the same DEFINE conditions to each row (one symbol: `D`/`F`/`U`, or `C` for "same trip"; the first
row of a partition has `PREV() = NULL`, so only `strt` takes it) and runs the PATTERN as a Python regular expression:
`.D+(?:F|D)*U+` and `.C*`. Python's `re` is leftmost and greedy with backtracking, and `finditer` is non-overlapping,
which is `AFTER MATCH SKIP PAST LAST ROW`. The cloud job must return exactly the same rows.

## Result (local reference)
| SKU | Dip | Before | Cut on | Promo | Back on | Back to | Days |
|---|---|---|---|---|---|---|---|
| P01 Coffee 12oz | 1 | $9.99 | 10-17 | $7.99 | 10-23 | $9.99 | 8 |
| P02 Greek yogurt 32oz | 1 | $5.49 | 10-15 | $4.49 | 10-21 | $5.49 | 8 |
| P02 Greek yogurt 32oz | 2 | $5.49 | 10-24 | $4.99 | 10-27 | $5.49 | 5 |
| P05 Cereal 18oz | 1 | $4.49 | 10-16 | $3.49 (two steps) | 10-24 | $4.49 (via $3.79) | 10 |

4 TPRs in 3 of 6 SKUs, deepest 22.3% (cereal). 84 scans on 8 cards make **22 trips**, up to 4 per card; every gap
between trips is over 45 minutes and every scan is in exactly one trip (the test checks both).

**Cloud:** not run yet. The Databricks Free Edition workspace has refused compute since 2026-10-24
(`FEATURE_DISABLED` / "Organization ... not active"), so `run_cloud.py` warns and the feature is `built`. Once the
account is active again, the daily workflow submits the job and it must match these rows.

## Why it matters
TPR detection and sessionization are classic "look at the previous row" problems that usually need a stack of
`LAG`, running sums and gaps-and-islands CTEs. A row pattern says it directly, handles stepped cuts with one
alternation, and the measures (`FIRST`, `LAST(down.price)`, `MATCH_NUMBER()`) come out of the same pass. Being
Beta, only `AFTER MATCH SKIP PAST LAST ROW` is supported and `PARTITION BY` takes columns only.

## How to run
```bash
python features/databricks/day-35-shelf-price-patterns/price_patterns.py     # local reference, writes result.json
pytest -q tests/test_day35_databricks_match_recognize.py                     # reference, SQL, fake REST API
bash features/databricks/day-35-shelf-price-patterns/cloud.sh                # needs DATABRICKS_HOST / _TOKEN
```
`cloud.sh` uploads the script to the caller's workspace folder, submits a one-off serverless job (environment
version 6, no SQL warehouse), reads the result file from a scratch Unity Catalog volume, compares, and deletes the
folder, volume and schema.

## Cost
One serverless job task of under a minute on a few hundred rows: within Free Edition's daily quota, $0. Nothing is
left behind.
