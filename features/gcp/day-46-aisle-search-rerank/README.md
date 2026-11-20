# Day 46 · Google Cloud · Two-stage aisle finder: AI.SEARCH with rerank

**New platform feature:** **`AI.SEARCH` `rerank` option** (GA, **2026-11-17**): vector search retrieves candidates,
then a ranking model reorders them, in the same `AI.SEARCH` call. From the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) (November 17, 2026, the same day
as `AI.RANK`); reference: [`AI.SEARCH`](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-search).

## What it does
Day 44 searched the catalog by embeddings only (6-7/9 shopper queries right); day 45 ran `AI.RANK` over **every** row
(9/9). Neither scales cleanly: embeddings miss intent, and ranking every row of a real catalog is slow and costly.
The usual answer is two stages: cheap vector retrieval of a few candidates, then a ranker on just those.

[`aisle_search_rerank.py`](aisle_search_rerank.py) and [`cloud.sh`](cloud.sh):
1. create day 44's 12-product table with a `GENERATED ALWAYS AS (AI.EMBED(description, model => 'embeddinggemma-300m'))
   STORED OPTIONS (asynchronous = TRUE)` column and wait until BigQuery has embedded every row (no embedding is
   written by hand);
2. for each of the 9 shopper queries, `AI.SEARCH(TABLE products, 'description', query, top_k => 5, rerank => ...)`:
   top 5 by vector distance, reordered by the ranking model;
3. check the first product after reranking against the day-44 answer key, and report how often the answer was among
   the 5 candidates, how vector distance alone would have done, and how many top picks the rerank changed.

**Call form probing.** The `AI.SEARCH` reference page couldn't be read from the session that built this (no web page
was readable that morning), so `cloud.sh` tries four argument shapes with the "pasta sauce" query and uses the first
one BigQuery accepts; the annotation names it, and if none is accepted every form's error is annotated for a
fix-forward (as day 45 did):

| Form | Extra arguments |
|---|---|
| 1 | `top_k => 5, rerank => TRUE` |
| 2 | `top_k => 5, rerank => 'semantic-ranker-default@latest'` |
| 3 | `top_k => 5, options => JSON '{"rerank": true}'` |
| 4 | `rerank => TRUE` |

**Must hold** (else the run fails): every query returns at least 3 candidates with a rerank score or rank column, and
at least **7/9** queries put the expected product first.

## Why it matters
Retrieve-then-rerank is the standard pattern for search quality at catalog scale; with this option it's one SQL
function over a table that keeps its own embeddings, with no pipeline, no vector database and no ranking service to call
by hand.

## How to run
```bash
python features/gcp/day-46-aisle-search-rerank/aisle_search_rerank.py          # answer key + baselines, result.json
pytest -q tests/test_day46_gcp_search_rerank.py                                  # no credentials (fake bq / gcloud)
bash features/gcp/day-46-aisle-search-rerank/cloud.sh                            # GitHub Actions: GCP_PROJECT_ID
```

## Cost
A short-lived dataset with one 12-row table (deleted on exit), a few on-demand queries at the 10 MiB minimum, the
background embedding jobs (built-in model on BigQuery slots), and about 10 ranking calls of 5 records (list price $1
per 1,000 ranking queries: about $0.01). The Discovery Engine API that reranking uses was enabled by day 45.

## Result
Local: answer key and the day 44 / 45 baselines in [`result.json`](result.json). The cloud result (form accepted,
x/9 correct, answer-in-candidates, vector-only score, top picks changed) is the `Google Cloud day 46` annotation of
the run after merge, recorded in the daily report.
