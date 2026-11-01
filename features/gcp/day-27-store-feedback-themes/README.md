# Day 27 · Google Cloud · Store complaint themes as typed rows: AI.AGG with output_schema

**New platform feature:** **`AI.AGG` with the `output_schema` argument** (GA). `AI.AGG` is an aggregate function that
reads every row of a group and answers a natural-language instruction about them in one Gemini call per group (GA
**2026-10-13**); `output_schema` (GA **2026-10-14**) makes that answer a `STRUCT` with named, typed, optionally
`NOT NULL` fields instead of a `STRING`. Both in the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)
([reference: AI.AGG](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-agg)).

## What it does
24 customer comments from three stores ([`data/comments.csv`](data/comments.csv)) go through one query:

```sql
SELECT store, COUNT(*) AS comments,
       AI.AGG(comment, "...find the complaint raised most often...",
              endpoint => 'gemini-3.5-flash',
              output_schema => 'top_issue STRING NOT NULL OPTIONS(description = "one of: checkout_wait, ..."),
                                mentions INT64 NOT NULL, summary STRING NOT NULL, suggested_fix STRING NOT NULL')
FROM ... GROUP BY store
```

Each store gets one row: the top complaint as one of six fixed categories, how many comments raise it, a one-line
summary and a suggested fix. The comments are inlined (`UNNEST`), so nothing is created.

## Why it matters
"What are people complaining about at each store?" used to mean either a hand-written classifier or a free-text LLM
answer that had to be parsed. With `output_schema` the answer is already a typed column a dashboard or an alert can
filter on (`WHERE themes.top_issue = 'out_of_stock'`), straight from `GROUP BY`.

## How to run
```bash
python features/gcp/day-27-store-feedback-themes/themes.py        # local answer key, writes result.json
python features/gcp/day-27-store-feedback-themes/themes.py --sql  # the BigQuery SQL
pytest -q tests/test_day27_gcp_ai_agg_output_schema.py
```
The local run can't call Gemini, so each comment carries a hand label (`issue`) and the answer key is the most common
label per store: east `checkout_wait` (6 of 8), north `out_of_stock` (6 of 8), south `produce_quality` (5 of 8).
`cloud.sh` (GitHub Actions, keyless) runs the query and checks what must hold whatever the wording: one `STRUCT` per
store, all four fields present, `top_issue` one of the six categories, `mentions` between 1 and the store's comment
count. Agreement with the answer key is reported; a different pick is a warning (a finding about the model).

## Cost
One query over inlined data (0 bytes scanned) and three Gemini calls of about 300 input tokens each: a fraction of a
cent, inside the free-trial credits.

## Result
Answer key: 3 stores, 24 comments, one query. Cloud result: see the day's report. Full numbers in
[`result.json`](result.json).
