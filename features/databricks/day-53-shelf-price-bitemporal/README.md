# Day 53 · Databricks · Shelf-price history on two time axes with bitemporal AUTO CDC

**New platform feature:** **bitemporal AUTO CDC** in Lakeflow Spark Declarative Pipelines: `STORED AS BITEMPORAL`
with a required `SYSTEM SEQUENCE BY` column (**Beta**, Databricks Runtime 17.3+). The target keeps
`__START_AT` / `__END_AT` (business time, from `SEQUENCE BY`) **and** `__SYSTEM_START_AT` / `__SYSTEM_END_AT` (system
time); late or restated changes close the old belief instead of overwriting it. Documented on **2026-11-20** in
Databricks' official [agent skills](https://github.com/databricks/databricks-agent-skills)
([`databricks-pipelines/references/auto-cdc-sql.md`](https://github.com/databricks/databricks-agent-skills/blob/main/skills/databricks-pipelines/references/auto-cdc-sql.md),
Pattern 11 and "Querying Bitemporal Tables"), which is the date in the manifest. The Databricks release-notes and SQL
reference pages couldn't be read from the build session (permission prompt unanswered), so the date of the docs
page itself isn't confirmed; it isn't in the pipelines release notes read earlier this month (they stopped at
September).

## What it does
A store's price feed sends 9 price changes in 5 batches during Thanksgiving week. Each change carries the November
day it takes effect on the shelf (`effective_day`, business time) and the batch that reported it (`feed_batch`,
system time). Three batches rewrite the past:

| Batch | What it says | Why it's hard |
|---|---|---|
| 1 | turkey 1.49, cranberry 2.99, stuffing 3.49, all from the 20th | |
| 2 | turkey 0.99 from the 24th, stuffing 2.99 from the 23rd | promos announced ahead |
| 3 | turkey 1.29 from the **22nd** | late: out of order in business time |
| 4 | cranberry **2.79 from the 20th** | restatement: same day, new belief |
| 5 | stuffing 3.49 from the 23rd, pie crusts 4.99 from the 25th | promo cancelled; new SKU |

```sql
CREATE OR REFRESH STREAMING TABLE shelf_prices;
CREATE FLOW shelf_price_flow AS AUTO CDC INTO shelf_prices
FROM STREAM(price_feed)
KEYS (sku)
SEQUENCE BY effective_day          -- business time: the shelf day
SYSTEM SEQUENCE BY feed_batch      -- system time: when we learned it
STORED AS BITEMPORAL;
```

Batches 1-2 land before the first pipeline update, 3-5 before the second. The check asks every as-of question:
"as of batch S, what price did we think was on the shelf on day B?" for 4 SKUs x days 20-26 x every batch seen, with
the two-axis filter from the skill (`__START_AT <= B AND (__END_AT > B OR NULL) AND __SYSTEM_START_AT <= S AND
(__SYSTEM_END_AT > S OR NULL)`). Answers, not raw rows, are compared, so a different row split in the engine can't
fail a correct result. Locally three references must agree: a Python row builder, DuckDB running the as-of filter
over those rows, and DuckDB answering straight from the raw feed.

## Why it matters
A shelf-tag audit, a price-match claim or a margin report asks two different questions: what the price *was* on the
22nd (now known: 1.29) and what we *thought* it was when the tag was printed (1.49 as of batch 2). SCD type 2 keeps
only the first. Bitemporal keeps both with one extra clause, and no correction ever deletes history.

## How to run
```bash
python features/databricks/day-53-shelf-price-bitemporal/bitemporal.py         # local references, writes result.json
python features/databricks/day-53-shelf-price-bitemporal/bitemporal.py --sql   # the pipeline SQL
pytest -q tests/test_day53_databricks_bitemporal_cdc.py                       # no credentials needed
bash features/databricks/day-53-shelf-price-bitemporal/cloud.sh              # GitHub Actions (Free Edition)
```

## Cost
Free Edition: one serverless pipeline in development mode, two short updates, a handful of SQL statements on the
serverless warehouse; pipeline, workspace folder and scratch schema are deleted at the end. $0.

## Result
Local (all three references agree after both rounds): **9 price changes -> 12 bitemporal rows, 6 in the current
view, 6 closed beliefs**; **40 of 140 as-of answers were later revised**. Turkey on the 22nd: 1.49 as of batch 2,
1.29 now; cranberry on the 21st: 2.99 as of batch 3, 2.79 now; stuffing on the 23rd: 2.99 as of batch 4, 3.49 now.
Pie crusts are unknown before batch 5. Numbers in [`result.json`](result.json).

**Cloud:** not run yet. The Free Edition workspace has refused compute since 2026-10-30 (jobs `FEATURE_DISABLED`,
warehouse "could not be processed"); `cloud.sh` treats that as a warning, as days 40-52 do. If the workspace is
active again and the Beta needs turning on, the warning names it.
