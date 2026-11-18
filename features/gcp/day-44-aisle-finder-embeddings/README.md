# Day 44 · Google Cloud · An aisle finder whose embeddings keep themselves up to date

**New platform feature:** **Autonomous embedding generation** (GA, BigQuery release notes, **2026-06-17**): a column
declared `GENERATED ALWAYS AS (AI.EMBED(...)) STORED OPTIONS (asynchronous = TRUE)` that BigQuery fills and keeps
current with background jobs whenever rows are added or changed. This day uses the built-in
`embeddinggemma-300m` model, which runs on BigQuery slots with no connection and no Vertex AI charge.
Sources: [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) (June 17, 2026) and
[Autonomous embedding generation](https://docs.cloud.google.com/bigquery/docs/autonomous-embedding-generation).

## What it does
Shoppers type what they want ("hot cereal for breakfast", "something to wash the dishes after dinner"), and shelf
tags say something else ("Old-fashioned whole grain rolled oats for porridge", "Lemon scented liquid soap that cuts
grease"). A keyword search finds almost nothing. The table does the embedding work itself:

```sql
CREATE TABLE products (
  sku STRING, name STRING, aisle STRING, description STRING,
  description_embedding STRUCT<result ARRAY<FLOAT64>, status STRING>
    GENERATED ALWAYS AS (AI.EMBED(description, model => 'embeddinggemma-300m'))
    STORED OPTIONS (asynchronous = TRUE)
);
SELECT base.sku, distance FROM AI.SEARCH(TABLE products, 'description', 'hot cereal for breakfast');
```

[`cloud.sh`](cloud.sh), in a scratch dataset: inserts 11 products and waits for BigQuery to embed them, **then inserts
oat milk** and waits again (the loader never writes an embedding), then runs `AI.SEARCH` for 9 shopper queries, top 3
each. If `AI.SEARCH` is refused, it ranks with `ML.DISTANCE` over the same generated column instead and says so.

**Must hold** (else the run fails): all 12 rows embedded with an empty status and one embedding length; the late oat
milk embedded by the background job and in the top 3 for "dairy-free milk for my coffee"; at least 6 of the 9
queries put the expected product first. **Reported:** seconds each background fill took, hits out of 9, misses.

## Why it matters
Embedding pipelines usually mean a scheduled job that finds new or changed rows, calls a model and writes vectors
back, and they drift when someone forgets a row. Here the table owns that: insert a product and its vector shows up.
With the built-in model, the text never leaves BigQuery and there's no connection or IAM to set up.

## How to run
```bash
python features/gcp/day-44-aisle-finder-embeddings/aisle_finder.py      # baseline + answer key, writes result.json
pytest -q tests/test_day44_gcp_autonomous_embeddings.py                  # no credentials
bash features/gcp/day-44-aisle-finder-embeddings/cloud.sh                # GitHub Actions: GCP_PROJECT_ID (keyless)
```

## Cost
A few DML statements and queries over 12 rows (10 MiB on-demand minimum each, about $0.0001), polling queries while
the background jobs run, and the background jobs' own on-demand DML and slot use for 12 short descriptions. No
Vertex AI calls. Well under a cent. The dataset is deleted on exit (tables also expire after an hour).

## Result
Local: a word-overlap search (what `LIKE` / keywords would do) gets **1 of 9** queries right and finds no
overlapping word at all for 5 of them. The cloud result is in the day's report and the run's annotations. Numbers in
[`result.json`](result.json).
