# Day 24 · Databricks · One basket scan, three reports: `df.cache()` on serverless

**New platform feature:** **DataFrame caching on serverless compute** (Public Preview): `df.cache()` and
`df.persist()` now work on serverless notebooks and jobs with serverless environment version 5 or above. Announced in
the [Databricks platform release notes](https://docs.databricks.com/aws/en/release-notes/product/2026/october) on
**2026-10-23** (also in the [serverless compute release notes](https://docs.databricks.com/aws/en/release-notes/serverless)).
`df.unpersist()` is supported too; `CACHE TABLE`, `spark.catalog.cacheTable()` and `df.checkpoint()` still aren't
([limitations](https://docs.databricks.com/aws/en/compute/serverless/limitations)).

## What it does
[`basket_cache.py`](basket_cache.py) is a serverless job task. It builds the week's enriched basket lines once
(120,000 candidate scan lines → baskets of 2–7 lines, voided scans dropped, prices joined, line totals in cents),
caches them, and serves three reports from the cache:

```python
df = enriched_lines(spark)
df.cache()            # raised NOT_SUPPORTED_WITH_SERVERLESS before; supported on environment version 5+
rows = df.count()     # materialises the cache
reports(df)           # revenue by store, revenue by category, basket-size histogram: all read the cached lines
df.unpersist()
```

## Why it matters
Before this, a serverless job that fed several outputs from one expensive DataFrame either recomputed it for every
output or wrote it to a scratch table first. Now it caches like on classic compute, which matters on Free Edition
where serverless is the only compute.

## How to run
```bash
python features/databricks/day-24-basket-cache/basket_cache.py   # local pyspark; writes result.json
pytest -q tests/test_day24_databricks_df_cache.py
```
The test runs the same job code on local pyspark and checks it equals `result.json`, and runs the cloud runner
against a fake REST API (submit, poll, read back, compare, clean up, and the failed-job path).

`cloud.sh` → [`run_cloud.py`](run_cloud.py) (GitHub Actions, REST only, **no SQL warehouse**): imports the script
into the caller's workspace folder, creates a scratch schema and volume, submits a one-off `runs/submit` job with a
serverless environment `{"environment_version": "5"}`, waits for it, reads the result file from the volume (or the
task logs), and fails unless the cache flags and all three reports equal `result.json`. Folder, volume and schema are
deleted on exit; nothing is left scheduled.

## Cost
One short serverless job run on Databricks Free Edition (no charge; counts against the Free Edition daily quota).

## Result
| Store | Baskets | Items | Revenue |
|---|---|---|---|
| east | 4,286 | 33,935 | $171,210.53 |
| hq-express | 4,285 | 42,411 | $182,969.75 |
| north | 4,286 | 33,929 | $171,164.79 |
| west | 4,286 | 42,417 | $183,003.71 |

76,345 lines cached once (`Disk Memory Deserialized 1x Replicated` locally) and reused for 3 reports; top category
pantry ($210,092.12); 17,143 baskets, mostly 2–7 lines (60 one-line baskets left after voided scans). Numbers in
[`result.json`](result.json).
