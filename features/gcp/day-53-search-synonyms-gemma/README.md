# Day 53 · Google Cloud · Search-box synonyms: "courgette" finds Zucchini with AI.SIMILARITY on embeddinggemma-300m

**New platform feature:** the **`embeddinggemma-300m`** model in BigQuery's `AI.EMBED` and `AI.SIMILARITY`,
generally available (GA) per the [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)
on **2026-11-23** (same entry: `embeddinggemma-2` in `AI.EMBED`, Preview).
[AI.SIMILARITY reference](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-similarity).
Unlike day 26's `gemini-embedding-2` (a Vertex AI endpoint), EmbeddingGemma is a **built-in** model, picked with
`model => 'embeddinggemma-300m'`; with `model`, the `endpoint`, `model_params` and `connection_id` arguments are not
allowed. No `CREATE MODEL`, no connection. Syntax follows
Google's [`bigquery-ai-ml` skill](https://github.com/google/skills/tree/main/skills/cloud/bigquery-ai-ml)
(`references/ai_similarity.md`).

## What it does
Shoppers type what they call a thing: `aubergine`, `kitchen roll`, `nappies`, `fizzy pop`. The catalog says
*Eggplant*, *Paper Towels*, *Diapers*, *Cola 12-Pack Cans*. 16 search terms ([`data/searches.csv`](data/searches.csv),
with an answer key) and 16 products ([`data/catalog.csv`](data/catalog.csv)) give 256 pairs, scored in one query:

```sql
SELECT search, sku,
       TO_JSON_STRING(AI.SIMILARITY(content1 => search, content2 => name, model => 'embeddinggemma-300m')) AS raw
FROM UNNEST([...search terms...]) AS search
CROSS JOIN UNNEST([STRUCT(... AS sku, ... AS name), ...])
```

A search's match is the product with the highest score. A second query scores the same pairs with
`gemini-embedding-2` so the two embedding models can be compared on the same task.

## Why it matters
Seven terms share a word with their product (`porridge oats` / *Old-Fashioned Rolled Oats*, `kitchen roll` /
*Paper Towels 6 Rolls*); nine share only meaning (`courgette` / *Zucchini*). A fuzzy matcher gets the first group and guesses on the rest; a search box that
returns *Cola* for "courgette" loses the sale. EmbeddingGemma is a small (300M-parameter) open embedding model, so
it is the cheap option for short product text: today checks whether it is good enough for synonyms.

## How to run
```bash
python features/gcp/day-53-search-synonyms-gemma/matcher.py              # local trigram baseline, writes result.json
python features/gcp/day-53-search-synonyms-gemma/matcher.py --sql        # the BigQuery SQL (embeddinggemma-300m)
pytest -q tests/test_day53_gcp_embeddinggemma_similarity.py
```
Embeddings can't run locally, so the local run is a character-trigram cosine baseline. `cloud.sh` (GitHub Actions,
keyless) runs both queries. It **fails** if the embeddinggemma-300m query fails or any of the 256 pairs is missing or
outside [-1, 1]; it **warns** if the model matches fewer searches than the trigram baseline or if the
gemini-embedding-2 comparison fails. Nothing is created or left behind.

## Cost
Two queries over inlined values (0 bytes scanned), each capped at 20 MiB billed, plus about 1,000 very short
embedding requests: a fraction of a cent, inside the free trial.

## Result
Local trigram baseline: **7 of 16** searches matched (43.8%), mean margin over the runner-up 0.108. It gets the
seven terms that share a word and misses all nine meaning-only ones (`aubergine` scores 0 against everything and
counts as no match; `courgette` goes to *Cola*, `nappies` to *Shortbread Cookies*, `crisps` to *Heavy Whipping
Cream*).

**Cloud, first run (2026-11-26, merge of PR #170): failed.** The query passed `endpoint => 'embeddinggemma-300m'`
and BigQuery answered `Unsupported remote model endpoint embeddinggemma-300m`. Fixed forward the same day: the
model is built in and takes `model =>` (as in BigQuery DataFrames' `bbq.ai.similarity(..., model=...)`, checked in
its source because the docs pages could not be read from this run).

**Cloud, after the fix (2026-11-26, merge of PR #171): passed.** All 256 pairs scored by both models.

| Matcher | Top-1 right | Mean margin over runner-up | Misses |
|---|---|---|---|
| Character trigrams (local) | 7 of 16 | 0.108 | all nine meaning-only terms |
| **`embeddinggemma-300m` (built-in)** | **14 of 16** | 0.094 | `rocket salad` → *Eggplant*, `fizzy pop` → *Eggplant* |
| `gemini-embedding-2` (Vertex AI) | 16 of 16 | 0.163 | none |

The small built-in model gets `courgette`, `aubergine`, `nappies`, `crisps`, `garbanzo beans` and `kitchen roll`
right with no Vertex AI endpoint; it misses the two terms whose meaning is a regional idiom (`rocket` for arugula,
`pop` for soda), where both go to the same product. Its margins are about half Gemini's, so a "needs review"
threshold has to be set per model (as day 26 found).
