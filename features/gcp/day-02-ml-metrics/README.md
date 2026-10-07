# Day 2 · Google Cloud · Grading grocery predictors with `ML.METRICS`

**New platform feature:** [`ML.METRICS` function](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-metrics)
(Preview), released **2026-09-10** ([BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)).

## What it does
Scores two simple "predictors" over 25 grocery products, straight from columns, with no model:

1. **Classification:** does the *organic* label predict a healthy product (health score ≥ 70)?
2. **Regression:** how well does the Nutri-Score letter alone (a=90, b=75, c=55, d=38, e=22) estimate the 0–100 score?

```sql
SELECT * FROM ML.METRICS(
  (SELECT organic AS predicted, health_score >= 70 AS actual FROM `products`),
  predicted_col => 'predicted', actual_col => 'actual', task_type => 'classification')
```

## Why it matters
Until now, BigQuery computed precision, recall, R² and friends only for a trained BigQuery ML model
(`ML.EVALUATE`). `ML.METRICS` grades **any** predicted-vs-actual columns: rule-based labels, `AI.CLASSIFY` output,
an external model's scores, or yesterday's forecast against today's actuals, in one SQL call.

## How to run
```bash
python metrics.py --local                          # pure Python with ML.METRICS' documented definitions; writes result.json
python metrics.py --compare cls.json reg.json      # compare BigQuery's JSON output with the local result
```
In CI, `cloud.sh` (keyless sign-in) loads the CSV into a short-lived dataset (1-hour table expiry, deleted on exit),
runs both `ML.METRICS` queries, and fails unless BigQuery's metrics match the local ones to 4 decimals. The test
(`tests/test_day02_gcp_ml_metrics.py`) checks the local metrics against a confusion matrix and DuckDB SQL.

## Cost
Two queries over a few KB, each capped at 20 MiB billed: inside the BigQuery free tier (1 TiB/month). No model is
trained or stored.

## Result (25 products)
| Organic → healthy? | Value |
|---|---|
| Precision | 0.667 (6 of 9 organic products are healthy) |
| Recall | 0.545 (5 of 11 healthy products aren't organic) |
| Accuracy | 0.68 |
| F1 | 0.60 |

| Nutri-Score letter → health score | Value |
|---|---|
| Mean absolute error | 3.16 points |
| Median absolute error | 3.0 points |
| R² | 0.980 |
| Explained variance | 0.980 |

Takeaway: the Nutri-Score letter is a near-perfect proxy for the score; the organic label is a coin-flip-plus at
best. The products are a small curated sample in the style of Open Food Facts (25 rows; an odd count keeps the
median unambiguous).
