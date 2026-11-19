# Day 45 · Google Cloud · Aisle finder that reads the question: shopper queries ranked with AI.RANK

**New platform feature:** **`AI.RANK` function** (GA, **2026-11-17**): "reorder a table's rows by how well the text in a
specified column answers a search query". From the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) (November 17, 2026);
reference: [`AI.RANK`](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-rank).
The [vector search introduction](https://docs.cloud.google.com/bigquery/docs/vector-search-intro) adds that it
evaluates the query and each row's text together and returns each row with a relevance score and rank position.

## What it does
Day 44 answered 9 shopper queries with `AI.SEARCH` over an embedding column and got **7/9**: "pasta sauce" found the
spaghetti (the marinara tag says neither word) and "dairy-free milk for my coffee" found whole milk, with oat milk #2.
Embedding search compares two vectors made separately; a ranker reads the question and the shelf tag together.

[`aisle_rerank.py`](aisle_rerank.py) loads the same 12 products as a plain table (no embedding column, no
connection) and [`cloud.sh`](cloud.sh) runs `AI.RANK` over the `description` column for each of the 9 queries, then
checks the first-ranked product against the day-44 answer key.

**Call form probing.** The `AI.RANK` reference page couldn't be read from the session that built this (the release
note could), so `cloud.sh` tries three call forms with the "pasta sauce" query and uses the first one BigQuery
accepts; the annotation names it, and if none is accepted every form's error is annotated for a fix-forward:

| Form | Call |
|---|---|
| 1 | `AI.RANK(TABLE products, 'description', query)` |
| 2 | `AI.RANK((SELECT sku, name, description FROM products), 'description', query)` |
| 3 | `AI.RANK(TABLE products, 'description', query, top_k => 12)` |

Each output row is read as `TO_JSON_STRING` of the whole row, so the check finds `sku` and the rank or score column
by name, whatever the output shape.

**Must hold** (else the cloud run fails): every query returns at least 3 ranked rows, and at least **7 of 9** queries
put the expected product first (day 44's embedding search: 7/9; word overlap: 1/9).

## Why it matters
Shoppers type what they want to do ("ingredients for an omelette"), shelf tags say what the product is. Embedding
search gets close; a ranker that reads both together fixes the near-misses that cost a sale. In SQL, over the
product table, with no model to deploy.

## How to run
```bash
python features/gcp/day-45-aisle-rerank/aisle_rerank.py    # baseline + answer key, writes result.json
pytest -q tests/test_day45_gcp_ai_rank.py                  # no credentials (includes cloud.sh against a fake bq)
bash features/gcp/day-45-aisle-rerank/cloud.sh             # GitHub Actions, keyless: GCP_PROJECT_ID
```

## Cost
A short-lived US dataset with one 12-row table (deleted on exit; tables also expire after an hour), one probe query
per tried call form and one ranking query: 9 queries x 12 short descriptions through AI.RANK's model, plus 10 MiB
on-demand minimums. Estimated well under one cent of the free-trial credits.

## Result
Local: word overlap puts the right product first for **1/9** queries; day 44's embedding search got **7/9**. The cloud
result (call form used, queries right, which day-44 misses were fixed) is in the first run's annotation and in the
report; numbers in [`result.json`](result.json).
