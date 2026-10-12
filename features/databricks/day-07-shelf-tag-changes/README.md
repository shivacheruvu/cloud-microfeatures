# Day 7 · Databricks · Which shelf tags to reprint? Automatic change data feed

**New platform feature:** [Automatic change data feed](https://docs.databricks.com/aws/en/tables/features/change-data-feed#auto-cdf),
**generally available 2026-09-01** ([product release notes, September 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/september);
also in [Databricks SQL 2026.36](https://docs.databricks.com/aws/en/sql/release-notes/2026), current channel 2026-09-08).
Row-level changes are computed at query time from **row tracking**, so `table_changes()` works without setting
`delta.enableChangeDataFeed` on the table, and `UPDATE` / `MERGE` don't write change files.

## What it does
A shelf-price table for two stores x six products is created with row tracking on and **no** change data feed
property. Four ordinary steps change it:

| Step | Statement | Change rows |
|---|---|---|
| 1 | `INSERT` the opening price list (tags printed) | 12 insert |
| 2 | `UPDATE`: supplier raises whole milk by 20 cents everywhere | 2 update pre + 2 post |
| 3 | `DELETE`: north drops oat milk | 1 delete |
| 4 | `MERGE` the weekly price feed (4 rows: 1 price change, 1 new item, 2 unchanged) | 1 pre + 1 post, 1 insert |

Then one query reads the whole history:

```sql
SELECT _commit_version, _change_type, store, barcode, product, price_cents
FROM table_changes('microfeatures_day07.shelf_prices', 0);
```

From the change rows after step 1 it builds the list of shelf tags to touch.

## Why it matters
"What changed since the tags were printed?" is a change-feed question. Before, you had to remember to switch on
change data feed per table before the changes happened, and every `UPDATE` / `MERGE` paid to write change files.
With automatic CDF, any table with row tracking can answer it later, and the feed's unchanged `MERGE` rows (north
bread, north bananas) correctly produce nothing.

## How to run
```bash
python shelf_changes.py --local        # DuckDB runs the same UPDATE / DELETE / MERGE; changes come from diffing
                                       # a snapshot after each step; writes result.json
python shelf_changes.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN; serverless SQL warehouse
pytest -q tests/test_day07_databricks_auto_cdf.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions. It fails if `delta.enableChangeDataFeed` is set (that would be
legacy CDF), if any step's net change per item (price before → after) differs from local, or if the final table
differs. If the exact change rows differ while the net effect agrees (an update shown as delete + insert), that's a
warning. `_commit_version` numbers are mapped to steps 1-4 by commit order. Everything it creates is dropped.

## Cost
About 12 small statements on a serverless SQL warehouse (trial credits; also fine on Free Edition). Nothing is kept.

## Result
| Store | Product | Was | Now | Tag |
|---|---|---|---|---|
| north | whole milk 1gal | $3.89 | $4.09 | reprint |
| north | oat milk 64oz | $4.99 | | remove |
| south | whole milk 1gal | $3.79 | $3.99 | reprint |
| south | large eggs 12ct | $4.19 | $3.89 | reprint |
| south | ginger kombucha 16oz | | $3.49 | new |

20 change rows in all; 2 of the 4 weekly-feed rows changed nothing. Full numbers in [`result.json`](result.json).
