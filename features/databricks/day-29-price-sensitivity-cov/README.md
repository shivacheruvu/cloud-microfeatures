# Day 29 · Databricks · Oat-milk price sensitivity from one `groupby().cov()`

**New platform feature:** **`DataFrameGroupBy.cov` in pandas API on Spark** (`pyspark.pandas`), listed in the
[Databricks serverless compute release notes](https://docs.databricks.com/aws/en/release-notes/serverless) under the
**2026-08-17** Databricks Runtime 19 updates (alongside `SeriesGroupBy.describe` and `is_valid_variant`). Upstream
it's [SPARK-40336](https://issues.apache.org/jira/browse/SPARK-40336) (`versionadded 4.3.0`), so the same line also
runs on local pyspark 4.3. It runs on serverless jobs, so it fits Databricks Free Edition.

## What it does
[`price_cov.py`](price_cov.py) builds 28 days of oat-milk sales for three stores (84 rows: price, units, promo flag)
and asks for every store's covariance matrix in one call:

```python
import pyspark.pandas as ps
psdf.groupby("store")[["price", "units", "promo"]].cov()   # was PandasNotImplementedError before this release
```

From each 3x3 matrix it reads:

- **raw price slope** `-cov(price, units) / var(price)`: extra units per $1 off;
- **price/promo correlation**: can price be told apart from the promo at all?
- if it can, the **two-variable slopes** `[b_price, b_promo] = Sxx⁻¹ Sxy`, straight from the same matrix.

## Result (local reference; the cloud job must match it to 2e-6)
| Store | Units (28 d) | Raw units per $1 cut | corr(price, units) | corr(price, promo) | Price effect, promo held | Promo effect, price held |
|---|---|---|---|---|---|---|
| east | 840 | 15.40 | -0.965 | **-1.000** | not separable | not separable |
| west | 820 | 19.20 | -0.984 | **-1.000** | not separable | not separable |
| north | 738 | 18.45 | -0.942 | -0.849 | **10.0** | 8.8 |

The raw slopes say west is most price sensitive, but in east and west price only moves on promo days, so the matrix
shows price and promo are perfectly confounded: those "slopes" are really promo lift. North raised its regular price
mid-month (4.49 to 4.99) without a promo, and that is enough for the matrix to separate the two: **10 units per $1**,
exactly the demand curve the data was generated with.

## Why it matters
Price-elasticity questions ("would a $0.50 cut sell more?") come down to per-group covariances. Before this release,
pandas-style code on serverless had to fall back to `covar_samp` for every column pair, or collect to the driver.
Now the pandas line works as written, and the matrix also tells you when the data can't answer the question.

## How to run
```bash
python features/databricks/day-29-price-sensitivity-cov/price_cov.py       # local reference, writes result.json
pytest -q tests/test_day29_databricks_groupby_cov.py
bash features/databricks/day-29-price-sensitivity-cov/cloud.sh             # needs DATABRICKS_HOST/TOKEN (CI)
```

The cloud run (REST only, no SQL warehouse) uploads `price_cov.py` to a workspace folder, submits it as a one-off
serverless job on **environment version 6**, reads the result from a scratch volume, compares every matrix entry and
the derived numbers with `result.json`, and deletes the folder, volume and schema. If the workspace refuses job runs
(the Free Edition account has been inactive since 2026-10-24), it reports a warning and verifies locally only.

## Cost
One short serverless job run on Free Edition: no charge (within the daily quota).

## Limits of this slice
The local test runs the real pandas-on-Spark call on local pyspark 4.3 and checks it, and plain pandas
`groupby().cov()`, against a pure-Python reference (sample covariance, `ddof=1`); it skips the Spark part only on an
older pyspark. The data is synthetic and small on purpose.
