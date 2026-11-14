# Day 40 · Google Cloud · Allergen flags from ingredient lists with gemini-4-carbon

**New platform feature:** **the `gemini-4-carbon` model in BigQuery generative AI functions** (GA,
**2026-11-10**): "BigQuery generative AI functions now support the gemini-4-carbon model." From the
[Google Cloud release notes](https://docs.cloud.google.com/release-notes) (BigQuery, November 10, 2026)
([reference: AI.GENERATE](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-generate)).

## What it does
14 deli and own-brand products with their ingredient lists ([`data/products.csv`](data/products.csv)): a protein bar
("whey protein isolate, cashew butter, cocoa, soy lecithin"), fresh pasta ("durum wheat semolina, eggs"), satay sauce
("roasted peanuts, coconut milk, soy sauce (soybeans, wheat)"), vegan cheddar ("coconut oil, potato starch")... The
shelf label needs **six yes/no flags per product**: milk, egg, peanut, tree nut, wheat, soy. One query asks
`AI.GENERATE` for six `BOOL` columns on the new model and, for comparison, on the default model:

```sql
SELECT product_no,
       AI.GENERATE(CONCAT("You label a grocery product for allergens... Ingredients: ", ingredients),
                   endpoint => 'gemini-4-carbon',
                   output_schema => 'milk BOOL, egg BOOL, peanut BOOL, tree_nut BOOL, wheat BOOL, soy BOOL') AS carbon,
       AI.GENERATE(..., endpoint => 'gemini-3.5-flash-lite', output_schema => ...) AS baseline
FROM UNNEST([...14 products...])
```

The products are inlined, so nothing is created. [`allergens.py`](allergens.py) holds the hand-worked answer key,
the SQL and the check. The traps are deliberate: whey, parmesan and butter are milk but coconut milk isn't; semolina is
wheat; soy sauce carries wheat and soy; peanuts aren't tree nuts and coconut isn't one either.

**Must hold** (else the cloud run fails): 14 rows; every `gemini-4-carbon` answer has an empty `status` and all six
flags as booleans. **Reported:** flags equal to the answer key (84 = 14 × 6) and products with every flag right, for
both models, with **missed allergens** (key yes, model no, the dangerous kind) listed apart from false alarms. A
`gemini-4-carbon` difference is a warning (a finding about the model, not a broken run).

## Why it matters
A keyword list misses "whey" and "semolina" and flags "coconut milk" as dairy; a label with a missed allergen is a
recall. A model reads the list the way a person does, `output_schema` gives typed flags a label template can use
directly, and swapping in a newer model is a one-word `endpoint` change you can check against your own answer key in
plain SQL before trusting it. This is a first pass for a person to confirm, not a replacement for one.

## How to run
```bash
python features/gcp/day-40-allergen-flags-carbon/allergens.py         # answer key, writes result.json
python features/gcp/day-40-allergen-flags-carbon/allergens.py --sql   # the BigQuery SQL
pytest -q tests/test_day40_gcp_gemini_4_carbon.py                       # no credentials needed
bash features/gcp/day-40-allergen-flags-carbon/cloud.sh               # GitHub Actions (keyless)
```

## Cost
One query, 0 bytes scanned; 28 short Gemini calls (14 products × 2 models, ~250 input tokens each) through the Vertex
AI API: well under one cent of free-trial credit per run.

## Result
Answer key (local): 14 products, 84 flags; products with milk 6, egg 6, peanut 2, tree nut 4, wheat 5, soy 4; 3 are
free of all six (vegan cheddar, hummus, rice crackers). The cloud run reports how many flags and products each model
gets right.

**Cloud run (2026-11-13, first run after the merge):** `gemini-4-carbon` answered all 14 products with six
booleans and got **84/84 flags right (14/14 products, no missed allergens, no false alarms)**; the default
`gemini-3.5-flash-lite` also got **84/84**. With the definitions spelled out in the prompt (coconut isn't a tree nut,
semolina is wheat) both models handle these lists; the new model ties the default here. Numbers in
[`result.json`](result.json).
