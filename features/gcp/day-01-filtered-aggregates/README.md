# Day 1 · Google Cloud · One-pass category scorecard with `WHERE` inside aggregates

**New platform feature:** a [`WHERE` clause inside an aggregate function call](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/aggregate-function-calls)
in BigQuery, Preview since **2026-09-14** ([release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)).

## What it does
Builds a grocery category scorecard from 24 products: how many products are Nutri-Score A or B, how many carry
high-risk additives, and the average health score for organic versus conventional products, all in one
`GROUP BY` pass.

```sql
COUNT(barcode WHERE nutri_grade IN ('a', 'b'))  AS grade_a_or_b,
AVG(health_score WHERE organic)                 AS avg_score_organic,
AVG(health_score WHERE NOT organic)             AS avg_score_conventional
```

## Why it matters
Filtered metrics usually mean `COUNTIF`, `AVG(IF(..., x, NULL))` or extra subqueries. The new syntax states
the filter where it applies, works for any aggregate, and keeps everything in a single scan. That's the same bytes
billed however many filtered metrics you add.

## How to run
```bash
python scorecard.py --local    # DuckDB (translates AGG(x WHERE c) to the standard FILTER clause); writes result.json
bash cloud.sh                  # needs gcloud/bq signed in and GCP_PROJECT_ID set
```
`cloud.sh` loads the CSV into a short-lived dataset (1-hour table expiry, deleted at the end anyway), runs the
query with a 20 MiB `maximum_bytes_billed` cap (BigQuery bills at least 10 MiB per query), and fails unless BigQuery's result matches the local one. In CI it
signs in keylessly (Workload Identity Federation).

## Cost
A few KB loaded and scanned: inside the BigQuery free tier (1 TB of queries and 10 GB of storage a month), so $0.

## Result (24 sample products)
| Metric | Value |
|---|---|
| Categories | 7 |
| Best category by average score | bakery |
| Nutri-Score A or B | 45.8% |
| Products with high-risk additives | 7 |
| Organic advantage (avg score points, categories with both) | +32.7 |

Full table: [`result.json`](result.json). Products are illustrative samples, not real brand data.
