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

The first run (2026-11-18) showed forms 1 and 2 parse and form 3 doesn't ("Named argument top_k not found in
signature for call to function AI.RANK"), and that `AI.RANK` calls the Vertex AI ranking API: both accepted forms were
refused with "the Discovery Engine API (discoveryengine.googleapis.com) is not enabled". Fixed forward the same day:
`cloud.sh` now enables that API once (the CI service account is a project editor, as for day 43's Document AI).

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
and one ranking query (10 MiB on-demand minimums each). AI.RANK uses the Vertex AI ranking API, list price **$1 per
1,000 ranking queries** of up to 100 records ([Vertex AI Search pricing](https://cloud.google.com/generative-ai-app-builder/pricing)):
10 calls (probe + 9 queries) of 12 records = about **$0.01 per run**, about $0.35 for the daily re-runs to day 80, inside
the free-trial credits. Enabling the Discovery Engine API itself costs nothing.

## Result
**Cloud (2026-11-18, after the fix-forward): AI.RANK put the expected product first for 9/9 queries**, with call
form 1 (`AI.RANK(TABLE products, 'description', query)`), every product ranked for every query (12 rows each).

| Approach | Right first | "pasta sauce" | "dairy-free milk for my coffee" |
|---|---:|---|---|
| Word overlap (local) | 1/9 | Marinara sauce | Whole milk |
| Day 44: `AI.SEARCH` over `embeddinggemma-300m` embeddings | 7/9 (6/9 on the 11-18 re-run) | Spaghetti | Whole milk (oat milk #2) |
| **Day 45: `AI.RANK`** | **9/9** | **Marinara sauce** | **Oat milk** |

Both of day 44's misses are fixed and nothing new is missed. Local baseline and answer key in
[`result.json`](result.json).
