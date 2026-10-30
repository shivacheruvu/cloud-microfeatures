# Day 25 · Google Cloud · Size a Gemini shelf-tag job before running it: AI.COUNT_TOKENS

**New platform feature:** **`AI.COUNT_TOKENS`** (GA): returns the number of tokens a Gemini model's tokenizer
produces for a text string, without making a billable generation call. Announced GA in the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) on **2026-10-27**
([reference](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-count-tokens),
[cost-management post with the call shape](https://cloud.google.com/blog/products/data-analytics/using-bigquerys-generative-ai-cost-management-features)).

## What it does
The plan: write a shelf-tag blurb for each of 24 grocery products ([`data/products.csv`](data/products.csv)) with
`gemini-3.1-flash-lite`. Two prompt templates ([`data/prompts.json`](data/prompts.json)) do the same job: a
**verbose** one (role-play preamble, rules as prose, labelled fields) and a **short** one (rules as a terse list,
fields inline). One BigQuery query counts the input tokens of all 48 prompts:

```sql
SELECT barcode, variant, TO_JSON_STRING(AI.COUNT_TOKENS(prompt, endpoint => 'gemini-3.1-flash-lite')) AS raw
FROM UNNEST([STRUCT(... AS barcode, ... AS variant, ... AS prompt), ...])
```

## Why it matters
Input tokens are most of the bill for a short-output job like this one, and they scale with the catalog. Counting them
first (free of generation calls) turns "which prompt?" into a number before anything is generated, and gives the
figure needed to set the AI-function token quotas.

## How to run
```bash
python features/gcp/day-25-shelf-blurb-token-budget/token_budget.py          # local estimate, writes result.json
python features/gcp/day-25-shelf-blurb-token-budget/token_budget.py --sql    # the BigQuery SQL
pytest -q tests/test_day25_gcp_count_tokens.py
```
Gemini's tokenizer can't run locally, so the local run uses a word-piece stand-in (a token per digit or punctuation
mark, a token per 4 letters of a word). `cloud.sh` (GitHub Actions) runs the query and checks what must hold whatever
the exact counts: all 48 prompts get a positive count, the short template is smaller for every product, the saving is
within 15 points of the local estimate, and the total is within 0.5x-2x of it. The real counts go into the notice.
The prompts are inlined in the SQL, so nothing is created or left behind.

## Cost
One query over inlined values (0 bytes scanned); `AI.COUNT_TOKENS` makes no generation call. Well inside the free
trial.

## Result
Local estimate: verbose 4,745 input tokens, short 1,697, **64.2% fewer** with the short template, smaller for all 24
products; largest short prompt is the plant-based burger (103 tokens, long ingredient list). Cloud counts: see the
report once the run is in. Full numbers in [`result.json`](result.json).
