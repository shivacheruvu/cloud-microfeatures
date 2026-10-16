# Day 11 · Google Cloud · Scan anomalies with TimesFM 3.0 in `AI.DETECT_ANOMALIES`

**New platform feature:** **TimesFM 3.0** in BigQuery (**Preview, 2026-10-07**,
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)). The release note says the new
model works with `AI.FORECAST`, `AI.EVALUATE` and **`AI.DETECT_ANOMALIES`** ("Use `AI.DETECT_ANOMALIES` to perform
univariate time series anomaly detection"). Day 10 used it for forecasting; this day uses it for anomaly
detection. Reference: [AI.DETECT_ANOMALIES](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-detect-anomalies)
(the reference page still lists only TimesFM 2.0 / 2.5 as `model` values; the release note adds 3.0).

## What it does
12 weeks of daily scan counts for three staples at one store (bananas, milk, sandwich bread: weekday pattern,
slight drift, ±6% noise). The first 8 weeks are history; the last 4 weeks (84 store-days) are checked. Three
problems are planted in those 4 weeks:

| Day | Product | What happened | Units | Usual for that weekday |
|---|---|---|---|---|
| Sun 2026-09-20 | bananas (lb) | scanner outage | 8 | 237 |
| Tue 2026-09-29 | milk 1 gal | duplicate scans | 297 | 116 |
| Wed 2026-10-07 | sandwich bread | short delivery | 36 | 81 |

One BigQuery query checks all three series at once:

```sql
SELECT * FROM AI.DETECT_ANOMALIES(
  TABLE scan_history, TABLE scan_target,
  data_col => 'units', timestamp_col => 'day', id_cols => ['product'],
  model => 'TimesFM 3.0', anomaly_prob_threshold => 0.95)
```

It returns, per product and day, `is_anomaly`, the expected `lower_bound` / `upper_bound` and an
`anomaly_probability`. No model is trained or stored, and no dataset is created.

## Why it matters
A broken scanner, a double-scan glitch or a short delivery all look like "sales" in the data and quietly poison
forecasts, reorder points and promo analysis. Zero-shot anomaly detection in SQL, across many products in one call,
is a cheap daily guard to run before anything downstream reads the numbers. TimesFM 3.0 is the same model day 10
forecast with, so one model now covers forecasting and data checks.

## How to run
```bash
python features/gcp/day-11-scan-anomalies/anomalies.py --local     # pure Python baseline; writes result.json
python features/gcp/day-11-scan-anomalies/anomalies.py --sql       # print the BigQuery query
pytest -q tests/test_day11_gcp_timesfm3_anomalies.py
```
`cloud.sh` runs the query in GitHub Actions (keyless) and fails if any target point is missing or has a status
message, or if TimesFM 3.0 flags fewer than 2 of the 3 planted problems; it warns on one miss or more than 4 false
alarms among the 81 normal store-days. If BigQuery rejects `TimesFM 3.0` here, the run fails and repeats the query
with TimesFM 2.5 for diagnosis only.

## Cost
One query over a few KB of inlined data, capped at 20 MiB billed (well inside the free tier). Nothing is created.

## Result
Local baseline (weekday median of the last 4 history weeks, flag beyond ±30%): catches **3 of 3** planted problems
with **0** false alarms on this clean synthetic data. The cloud result (TimesFM 3.0's flags and bounds) is added
after the post-merge run.

Full numbers in [`result.json`](result.json).
