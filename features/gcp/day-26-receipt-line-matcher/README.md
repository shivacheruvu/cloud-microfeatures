# Day 26 · Google Cloud · Match receipt abbreviations to products: AI.SIMILARITY on gemini-embedding-2

**New platform feature:** the **`gemini-embedding-2`** model in BigQuery's embedding functions (`AI.EMBED`,
`AI.SIMILARITY`, `AI.GENERATE_EMBEDDING`), generally available (GA) per the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) on **2026-10-05**
([AI.SIMILARITY reference](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-similarity)).
It needs no `CREATE MODEL` and no connection: the function names the endpoint.

## What it does
Receipts print lines like `GRND BF 80/20`, `PB CRMY` or `BATH TISSUE 12PK`. To roll basket data up by product,
each line has to be matched to the catalog. 16 receipt lines ([`data/receipt_lines.csv`](data/receipt_lines.csv),
with an answer key) and 16 products ([`data/catalog.csv`](data/catalog.csv)) give 256 pairs. One BigQuery query
scores them all:

```sql
SELECT line, barcode,
       TO_JSON_STRING(AI.SIMILARITY(content1 => line, content2 => name, endpoint => 'gemini-embedding-2')) AS raw
FROM UNNEST([...receipt lines...]) AS line
CROSS JOIN UNNEST([STRUCT(... AS barcode, ... AS name), ...])
```

A line's match is the product with the highest score (`matcher.py`).

## Why it matters
A fuzzy string matcher only sees shared letters. It does fine on `ORG BNNA` → *Organic Bananas*, but has nothing to go
on for `EVOO` (*Extra Virgin Olive Oil*) or `BATH TISSUE` (*Toilet Paper*). An embedding model can match on meaning,
and with `AI.SIMILARITY` it takes one SQL expression, no model to deploy.

## How to run
```bash
python features/gcp/day-26-receipt-line-matcher/matcher.py          # local trigram baseline, writes result.json
python features/gcp/day-26-receipt-line-matcher/matcher.py --sql    # the BigQuery SQL
pytest -q tests/test_day26_gcp_ai_similarity.py
```
Gemini embeddings can't run locally, so the local run is a character-trigram cosine baseline. `cloud.sh` (GitHub
Actions) runs the query and checks what must hold whatever the exact scores: all 256 pairs come back with a score in
[-1, 1]. Gemini's top-1 accuracy goes into the notice next to the baseline's; matching fewer lines than the baseline
is a warning (a finding about the model), not a failure. Nothing is created or left behind.

## Cost
One query over inlined values (0 bytes scanned) plus 512 short embedding requests (two per pair, a few tokens each):
a fraction of a cent. Well inside the free trial.

## Result
Local trigram baseline: **14 of 16** lines matched (87.5%), mean margin over the runner-up 0.17. It misses the two
lines that share no letters with their product: `EVOO` and `BATH TISSUE 12PK` (both score 0 against everything).

**Cloud (2026-10-30, `gemini-embedding-2`):** all 256 pairs scored; the top-1 match was right for **16 of 16** lines
(100%), including `EVOO` → *Extra Virgin Olive Oil* and `BATH TISSUE 12PK` → *Toilet Paper*, against 14 of 16 for the
trigram baseline. Mean margin over the runner-up 0.138 (baseline 0.170: embedding scores sit closer together, so a
margin threshold for "needs review" has to be set per model). The first run found the Vertex AI API disabled;
`cloud.sh` now enables it. Full local numbers in [`result.json`](result.json).
