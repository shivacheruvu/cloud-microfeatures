# Day 47 · Google Cloud · Shelf diet badges with multi-label AI.CLASSIFY

**New platform feature:** **multi-label `AI.CLASSIFY`** (`output_mode => 'multi'`, GA, **2026-11-17**): BigQuery's
`AI.CLASSIFY` can now return every matching category as an `ARRAY<STRING>` (zero or more labels) instead of exactly
one `STRING`. From the [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) (November 17,
2026: optimized mode and multi-label for `AI.IF` / `AI.CLASSIFY`)
([reference: AI.CLASSIFY](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-classify)).
The argument shape follows the upstream [google/skills](https://github.com/google/skills) BigQuery AI reference
(`skills/cloud/bigquery-ai-ml/references/ai_classify.md`).

## What it does
14 grocery products with their ingredient lists ([`data/products.csv`](data/products.csv)) get **diet badges** for
the shelf tag: `vegan`, `vegetarian`, `gluten_free`, `dairy_free`, `contains_nuts`. Hummus earns four, a chicken
caesar wrap none, pecan ice cream three. One query asks multi-label `AI.CLASSIFY` and, as a baseline, the default
single-label `AI.CLASSIFY` on the same input:

```sql
SELECT product_no,
       AI.CLASSIFY(CONCAT('Grocery product and its ingredients: ', item),
                   categories => [('vegan', 'no animal products at all: ...'), ('vegetarian', '...'), ...],
                   output_mode => 'multi') AS multi,      -- ARRAY<STRING>, zero or more badges
       AI.CLASSIFY(..., categories => [...]) AS single    -- STRING, exactly one
FROM UNNEST([...14 products...])
```

Nothing is created. [`badges.py`](badges.py) holds the hand-worked answer key, the SQL and the check. The traps:
honey is vegetarian but not vegan; plain oats aren't gluten-free but certified ones are; peanuts count as nuts,
coconut doesn't; soy sauce carries wheat. Because the reference page couldn't be read from the build session,
`cloud.sh` tries three call forms and names the one BigQuery accepts: (1) labels with descriptions, (2) plain labels
with the definitions in the input, (3) form 2 without the single-label baseline.

**Must hold** (else the cloud run fails): 14 rows; every multi-label answer is an array (empty allowed) of labels from
the five badges. **Reported:** badge-level precision, recall and F1 over the 36 badges in the key, products with
exactly the right set, missed and extra badges, and how many badges single-label surfaced. F1 under 0.8 is a warning.

## Why it matters
A shelf tag, a search filter or a diet-aware recommender needs *all* the labels that apply, not the "best" one. With
single-label classification the best possible recall here is 13/36 (one badge per product that has any); multi-label
is the same function with one more argument, returning an array a filter can `UNNEST` or test with `IN UNNEST(...)`.
It's a first pass for a person to confirm, not a replacement for one.

## How to run
```bash
python features/gcp/day-47-shelf-badges-multilabel/badges.py          # answer key, writes result.json
python features/gcp/day-47-shelf-badges-multilabel/badges.py --sql 1  # the BigQuery SQL (call form 1)
pytest -q tests/test_day47_gcp_multilabel_classify.py                   # no credentials needed
bash features/gcp/day-47-shelf-badges-multilabel/cloud.sh             # GitHub Actions (keyless)
```

## Cost
One query, 0 bytes scanned; 28 short Gemini calls (14 products, multi and single) through the Vertex AI API: well
under one cent of free-trial credit per run. Nothing to clean up.

## Result
Answer key (local): 14 products, 36 badges (vegan 5, vegetarian 11, gluten-free 8, dairy-free 8, contains nuts 4);
7 products carry 3 or more badges, 1 carries none. Single-label's ceiling: recall 0.361 (13/36). The cloud run reports
multi-label precision / recall / F1 and the single-label baseline. Numbers in [`result.json`](result.json).

**Cloud run (2026-11-20, first run after the merge):** call form 1 (labels with descriptions, `output_mode =>
'multi'`) was accepted. Multi-label `AI.CLASSIFY` returned an array for all 14 products with **precision 1.0, recall
0.889, F1 0.941** over 36 badges: **no false badges**, 11/14 products with exactly the right set. Missed: gluten-free
on the pecan ice cream and the Greek yogurt, vegan and dairy-free on the cashew stir-fry sauce (it is cautious rather
than wrong-way: a missing badge hides a product from a filter, a false one would mislead a shopper). Single-label on
the same input got 13/36 badges, its ceiling.
