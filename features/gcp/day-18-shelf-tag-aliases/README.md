# Day 18 · Google Cloud · Weekly-ad deal finder with reusable column aliases

**New platform feature:** **reusable column aliases** in GoogleSQL, **generally available 2026-10-15** (opt-in):
[release note](https://docs.cloud.google.com/bigquery/docs/release-notes) ("You can now use reusable column aliases
in GoogleSQL"). Reference: [Work with reusable column aliases](https://docs.cloud.google.com/bigquery/docs/column-aliases).
An alias defined in the `SELECT` list can be referenced later in the same `SELECT` list and in `WHERE`. It is off by
default and is turned on per project (or organization) and region with
``ALTER PROJECT ... SET OPTIONS (`region-REGION.reusable_column_aliases_mode` = "on")``.

## What it does
The weekly ad lists 25 items. Shelf-tag math is a chain (promo price → saving, price per kg, checkout price with
sales tax), and the ad only runs items that save at least **75c** and cost at most **$12.00 per kg** on promo:

```sql
SELECT
  sku, product, aisle, shelf_cents,
  DIV(shelf_cents * (100 - ad_pct) + 50, 100)              AS promo_cents,
  shelf_cents - promo_cents                                AS saving_cents,
  DIV(promo_cents * 1000 + DIV(pack_grams, 2), pack_grams) AS cents_per_kg,
  DIV(promo_cents * (10000 + tax_bp) + 5000, 10000)        AS checkout_cents
FROM shelf_items
WHERE saving_cents >= 75
  AND cents_per_kg <= 1200
ORDER BY cents_per_kg, sku;
```

Without the feature the same query writes the promo-price expression **6 times** (618 vs 452 characters), and a fix
to the rounding rule has to be made in six places. `cloud.sh` runs both versions and requires identical rows.

## Why it matters
Price and unit-price rules are exactly the kind of SQL that grows by copy-paste: each derived number depends on the
previous one, and the filter depends on the derived numbers. Before this, the choices were repeat the expression,
wrap it in a subquery/CTE per step, or move it into a UDF. Reusable aliases keep each step on one line in one query
block, which also makes Snowflake / Teradata queries that rely on lateral aliases portable as written.

## How to run
```bash
python features/gcp/day-18-shelf-tag-aliases/shelf_tags.py --local            # DuckDB; writes result.json
python features/gcp/day-18-shelf-tag-aliases/shelf_tags.py --sql aliases P.DS  # print a BigQuery statement
pytest -q tests/test_day18_gcp_reusable_aliases.py
```
`cloud.sh` (GitHub Actions, keyless) creates a short-lived US dataset and the 25-row table, runs the alias query and
the expanded control query, and compares both with the local result: a plain-Python model of the shelf-tag math,
which the test checks is identical to DuckDB's run of both queries (DuckDB already resolves aliases in `SELECT` and
`WHERE`; BigQuery's `DIV` is a one-line DuckDB macro). The GitHub Actions GCP job has no DuckDB installed. If the project hasn't opted in, the alias query fails with
`Unrecognized name`; the script then turns `region-us.reusable_column_aliases_mode` on, retries every 30 s for up to
10 minutes, and sets the option back to its default (NULL, off) on exit, so the project is left as it was.

## Cost
A CTAS and two queries over a 25-row table, each billed at the 10 MiB minimum and capped at 20 MiB (well under a
cent); `ALTER PROJECT` has no query charge. The dataset is deleted on exit and the table expires after an hour.

## Result
**14 of 25** ad items pass the deal rule (cents):

| SKU | Product | Aisle | Shelf | Promo | Saving | Per kg | Checkout |
|---|---|---|---|---|---|---|---|
| 0903 | oat milk 64oz | dairy alt | 479 | 359 | 120 | 190 | 359 |
| 6010 | vanilla ice cream 48oz | frozen | 649 | 389 | 260 | 286 | 389 |
| 3283 | honeycrisp apples 3lb | produce | 599 | 401 | 198 | 295 | 401 |
| 5121 | rolled oats 42oz | pantry | 599 | 449 | 150 | 377 | 449 |
| 5104 | marinara 24oz | pantry | 399 | 279 | 120 | 410 | 279 |
| 0703 | greek yogurt 32oz | dairy | 549 | 412 | 137 | 454 | 412 |
| 5110 | basmati rice 2lb | pantry | 549 | 439 | 110 | 484 | 439 |
| 4225 | avocados 4ct | produce | 499 | 399 | 100 | 499 | 399 |
| 2207 | bagels 6ct | bakery | 399 | 299 | 100 | 525 | 299 |
| 8010 | dish soap 19oz | household | 399 | 319 | 80 | 570 | 345 |
| 2201 | sourdough loaf | bakery | 499 | 399 | 100 | 587 | 399 |
| 8001 | paper towels 6ct | household | 1299 | 974 | 325 | 696 | 1054 |
| 6015 | frozen pizza | frozen | 799 | 599 | 200 | 788 | 599 |
| 7001 | tortilla chips 13oz | snacks | 449 | 301 | 148 | 816 | 301 |

Left out, for example: cheddar 8oz (30% off but 1229c per kg on promo), bananas and eggs (not discounted this week),
dark chocolate (70c saving).

**Cloud run note (2026-10-22):** the first post-merge run (#69) turned the opt-in setting on (it took effect after
about 90 s), but then the comparison step stopped because it imported DuckDB, which the GCP job doesn't install.
Fixed forward: the cloud comparison uses the plain-Python model.
