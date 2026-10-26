# Day 21 · Google Cloud · Will this shelf run out? A stockout classifier on XGBoost 2.1

**New platform feature:** the **`XGBOOST_VERSION` option** for BigQuery ML boosted-tree models, **GA, 2026-08-27**
in the [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) ("You can now train models
using XGBoost version 2.1 by using the `XGBOOST_VERSION` option"; BigQuery loads models and predicts with the XGBoost
3.1.0 library). Reference: [CREATE MODEL for boosted trees](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-create-boosted-tree).
Runs on on-demand billing, no reservation or Vertex AI needed.

## What it does
800 synthetic, seeded store-shelf days (one product on one store day): department, promo and weekend flags, opening
on-hand count, demand forecast and cover ratio (on-hand / forecast). The truth is whether actual demand (forecast x
department bias x promo lift 1.6 x weekend lift 1.25 x ±10% noise) beat the on-hand count, i.e. the shelf ran out.

```sql
CREATE OR REPLACE MODEL `<dataset>.stockout_xgb`
OPTIONS (model_type = 'BOOSTED_TREE_CLASSIFIER', xgboost_version = '2.1',
         input_label_cols = ['stockout'], data_split_method = 'NO_SPLIT',
         max_iterations = 40, max_tree_depth = 4, learn_rate = 0.3, early_stop = FALSE) AS
SELECT department, is_promo, is_weekend, on_hand, forecast_units, cover_ratio, stockout
FROM `<dataset>.shelf_days` WHERE split = 'train';
```

Then `ML.PREDICT` scores the 200 holdout shelf-days.

## Why it matters
Today's planner rule, "flag the shelf when on-hand is below the forecast", ignores promo and weekend lift, so it
misses **33 of 78** stockouts on the holdout (79.5% accuracy). A boosted tree learns the lifts from history. Pinning
`xgboost_version` keeps a retrain on the same XGBoost release as the model you validated, instead of whatever the
service default is that day.

## How to run
```bash
python features/gcp/day-21-stockout-xgboost/stockout.py --local     # data + baselines; writes result.json
pytest -q tests/test_day21_gcp_xgboost_version.py
```
`cloud.sh` runs in GitHub Actions (keyless): a short-lived dataset, `bq load` of the CSV, `CREATE MODEL` with
`xgboost_version = '2.1'`, `ML.PREDICT` on the holdout, and `bq show --model` to report the XGBoost version recorded in
the model's training options. It fails unless the model scores **at least 85%** on the holdout **and** beats the naive
rule by **10+ points**. The dataset (table + model) is deleted on exit.

The local check can't run BigQuery ML, so it scores the two rule baselines; as a one-off sanity check while building,
the open-source XGBoost 2.1.4 with the same hyperparameters (one-hot department) scored **95.5%** on the same holdout.

## Cost
A free load job, one `CREATE MODEL` and one `ML.PREDICT` over about 50 KB, each billed at the 10 MiB minimum and
capped at 20 MiB with `--maximum_bytes_billed`: well under one cent of free-trial credit.

## Result
| Holdout (200 shelf-days, 78 stockouts) | Accuracy | Missed stockouts |
|---|---|---|
| Naive rule `on_hand < forecast` | 79.5% | 33 |
| Rule with the true lifts (no noise; ceiling) | 97.5% | - |
| BigQuery ML boosted tree, XGBoost 2.1 | cloud run (must be ≥ 85% and ≥ naive + 10 pts) | cloud run |
