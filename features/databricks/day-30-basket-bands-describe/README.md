# Day 30 · Databricks · Basket-size bands per store from one `groupby()[col].describe()`

**New platform feature:** **`SeriesGroupBy.describe` in pandas API on Spark** (`pyspark.pandas`), listed in the
[Databricks serverless compute release notes](https://docs.databricks.com/aws/en/release-notes/serverless) under the
**2026-08-17** Databricks Runtime 19 updates ("`DataFrameGroupBy.cov` and `SeriesGroupBy.describe` in pandas API on
Spark"; day 29 used the first half). Upstream it ships in pyspark 4.3, so the same line runs locally too. It runs on
serverless jobs, so it fits Databricks Free Edition.

## What it does
[`basket_describe.py`](basket_describe.py) builds 40 checkout baskets for each of three stores (120 rows: store,
basket id, basket $) and asks for every store's summary in one call:

```python
import pyspark.pandas as ps
psdf.groupby("store")["basket"].describe()   # count, mean, std, min, 25%, 50%, 75%, max per store
```

From each store's row it sets two bands for the checkout team:

- **coupon threshold = 75th percentile**: baskets at or above it get the "big basket" $5-off-next-visit coupon;
- **bulk fence = Q3 + 1.5 × IQR**: baskets above it are catering / party orders, flagged for a manager check
  instead of a coupon.

## Result (local reference; the cloud job must match it to 2e-4)
| Store | Mean | Median | Std | Coupon at (Q3) | Bulk fence | Coupons | Bulk orders | Takings |
|---|---|---|---|---|---|---|---|---|
| downtown | $42.05 | $38.41 | 30.08 | **$48.33** | $80.70 | 10 | 1 | $1,681.94 |
| suburb | $87.45 | $80.39 | 43.73 | **$95.89** | $146.61 | 9 | 2 | $3,497.81 |
| campus | $20.87 | $20.66 | 5.97 | **$26.03** | $42.20 | 11 | 0 | $834.65 |

One store-wide threshold would hand every suburb family shop a coupon and none to campus; per-store bands give each
store about a quarter of its baskets. The three bulk orders ($212.40, $268.95, $241.10) sit far above the fences and
pull the means up (suburb's mean is $7.06 above its median, the most skewed), which is why the bands use quartiles.

**Quartiles are observed baskets.** pandas-on-Spark computes them with Spark's `percentile_approx`, so 25% / 50% / 75%
are real basket values (nearest rank), while plain pandas interpolates: downtown's Q3 is $48.33 on Spark vs $48.40
in pandas. The reference reproduces Spark's rule, the local pyspark run matches it exactly, and `result.json` keeps
the interpolated pandas quartiles alongside for comparison.

## Why it matters
Per-group summaries are the first thing anyone runs on new data. Before this release, pandas-style code on serverless
had to spell out `agg` with `percentile_approx` per statistic, or collect to the driver. Now the one-liner works as
written; just remember the quartiles won't be interpolated.

## How to run
```bash
python features/databricks/day-30-basket-bands-describe/basket_describe.py    # local reference, writes result.json
pytest -q tests/test_day30_databricks_groupby_describe.py
bash features/databricks/day-30-basket-bands-describe/cloud.sh                # needs DATABRICKS_HOST/TOKEN (CI)
```

The cloud run (REST only, no SQL warehouse) uploads `basket_describe.py` to a workspace folder, submits it as a
one-off serverless job on **environment version 6**, reads the result from a scratch volume, compares every
describe() value and the bands with `result.json`, and deletes the folder, volume and schema. If the workspace refuses
job runs (the Free Edition account has been inactive since 2026-10-24), it reports a warning and verifies locally only.

## Cost
One short serverless job run on Free Edition: no charge (within the daily quota).

## Limits of this slice
The data is synthetic and small on purpose. On large groups `percentile_approx` is approximate (default accuracy
10000), so quartiles could differ slightly from exact nearest rank; at 40 rows per store they're exact.
