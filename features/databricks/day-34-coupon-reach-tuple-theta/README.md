# Day 34 · Databricks · Coupon e-mail reach and spend with tuple × theta sketches

**New platform feature:** **`tuple_union_theta`, `tuple_intersection_theta`, `tuple_difference_theta`** (each with
`_double` and `_integer` variants), new SQL functions in
[Databricks SQL 2026.15](https://docs.databricks.com/aws/en/sql/release-notes/2026) (Preview channel **2026-05-26**,
Current 2026-06-03). They combine a **tuple sketch** (distinct keys *plus* a numeric summary per key) with a **theta
sketch** (distinct keys only). The same functions are in Apache Spark 4.2+, so this runs on local pyspark 4.3.

## What it does
[`coupon_reach.py`](coupon_reach.py): 1,800 loyalty purchases at 3 stores (600 customers) and a coupon e-mail list of
250 customers (every third customer, plus 50 who never shop). Each store keeps one tuple sketch,
`tuple_sketch_agg_double(customer_id, spend_cents, 12, 'sum')`; marketing ships one theta sketch of who got the e-mail,
`theta_sketch_agg(customer_id, 12)`: no spend, no raw ID list to join. Then, per store and chain-wide (store sketches
merged with `tuple_union_agg_double`):

| Question | Function |
|---|---|
| Which buyers got the e-mail, and what do they spend? | `tuple_intersection_theta_double(spend, mail, 'sum')` |
| Which buyers did the e-mail miss, and what do they spend? | `tuple_difference_theta_double(spend, mail)` |
| How many customers did we touch at all (bought or e-mailed)? | `tuple_union_theta_double(spend, mail, 12, 'sum')` (e-mail-only keys get 0.0) |

counted with `tuple_sketch_estimate_double` and summed with `tuple_sketch_summary_double(…, 'sum')`. With fewer keys
than the sketch's 2^12 nominal entries the sketches are exact, so Spark must equal the plain-Python set arithmetic.

## Result (local pyspark 4.3 equals the pure-Python reference)
| Store | Buyers | Spend | E-mail reached | Reached spend | Missed | Missed spend | Touched |
|---|---|---|---|---|---|---|---|
| S1 | 385 | $15,328.78 | 128 | $4,956.28 | 257 | $10,372.50 | 507 |
| S2 | 376 | $15,558.54 | 125 | $5,260.18 | 251 | $10,298.36 | 501 |
| S3 | 383 | $16,114.08 | 126 | $4,976.81 | 257 | $11,137.27 | 507 |
| **Chain** | **569** | **$47,001.40** | **189** | **$15,193.27** | **380** | **$31,808.13** | **630** |

The e-mail reached a third of the chain's buyers (189 of 569) and 32% of their spend; 61 of the 250 e-mails went to
people who didn't buy this period (50 never shop). Store buyers add up to 1,144 but the chain has 569: the merged
sketch dedupes customers who shop at more than one store while summing their spend.

## Why it matters
Sketches are the way to keep distinct counts mergeable across stores and days without storing raw customer IDs.
Before these functions, a tuple sketch (keys with spend) and a theta sketch (keys only, often what a partner or
marketing tool can share) couldn't be combined in SQL; you had to rebuild one side from raw IDs. Now a theta sketch
can filter, subtract or extend a tuple sketch directly and the spend summary comes along.

## How to run
```bash
python features/databricks/day-34-coupon-reach-tuple-theta/coupon_reach.py   # pure-Python reference -> result.json
pytest -q tests/test_day34_databricks_tuple_theta_sketches.py                # local pyspark run + fake REST API
bash features/databricks/day-34-coupon-reach-tuple-theta/cloud.sh             # needs DATABRICKS_HOST / _TOKEN
```
The cloud run (GitHub Actions) imports the script into the caller's workspace folder, submits it as a one-off
serverless job (environment version 6, no SQL warehouse), reads the result from a scratch volume (or the task logs),
compares it with `result.json` and deletes the folder, volume and schema. If the workspace refuses compute (the Free
Edition account has answered "not active" since 2026-10-24), it warns and exits cleanly.

## Cost
Free Edition serverless: one short job run (well under a minute of serverless compute) when the account is active;
$0 otherwise. Nothing is left running.
