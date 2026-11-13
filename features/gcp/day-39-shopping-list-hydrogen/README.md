# Day 39 · Google Cloud · Messy shopping-list lines to a pick quantity with gemini-4-hydrogen

**New platform feature:** **the `gemini-4-hydrogen` model in BigQuery generative AI functions** (GA,
**2026-11-12**): "BigQuery generative AI functions now support the gemini-4-hydrogen model." From the
[Google Cloud release notes](https://docs.cloud.google.com/release-notes) (BigQuery, November 12, 2026)
([reference: AI.GENERATE](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-generate)).

## What it does
14 lines a customer typed into a click-and-collect order ([`data/lines.csv`](data/lines.csv)): "2 dozen eggs",
"half a kilo of ground beef", "three 2L bottles of milk", "1.5 lb chicken thighs", "a baker's dozen bagels",
"750ml olive oil, two bottles", "a dozen and a half lemons"... The picker needs **one number in one base unit**
(each, g or ml) per line. One query asks `AI.GENERATE` for a typed answer on the new model and, for comparison, on the
default model:

```sql
SELECT line_no,
       AI.GENERATE(CONCAT("...one number in one base unit: each, g, ml... Line: ", line),
                   endpoint => 'gemini-4-hydrogen',
                   output_schema => 'item STRING, quantity FLOAT64, unit STRING') AS hydrogen,
       AI.GENERATE(..., endpoint => 'gemini-3.5-flash-lite', output_schema => ...) AS baseline
FROM UNNEST([...14 lines...])
```

The lines are inlined, so nothing is created. [`shopping_list.py`](shopping_list.py) holds the hand-worked answer key,
the SQL and the check.

**Must hold** (else the cloud run fails): 14 rows; every `gemini-4-hydrogen` answer has an empty `status`, a quantity
and a unit from {each, g, ml}. **Reported:** lines whose quantity is within 1 % of the answer key with the same unit,
for both models; a `gemini-4-hydrogen` miss is a warning (a finding about the model, not a broken run).

## Why it matters
Free-text quantities ("a dozen and a half", "two 400g tins", "1.5 lb") are where regex parsers give up and pickers
guess. A newer model is only worth switching to if it gets these right, and BigQuery lets you check that with a
one-word `endpoint` change on your own rows, side by side with the model you use today, in plain SQL.

## How to run
```bash
python features/gcp/day-39-shopping-list-hydrogen/shopping_list.py         # answer key, writes result.json
python features/gcp/day-39-shopping-list-hydrogen/shopping_list.py --sql   # the BigQuery SQL
pytest -q tests/test_day39_gcp_gemini_4_hydrogen.py                          # no credentials needed
bash features/gcp/day-39-shopping-list-hydrogen/cloud.sh                    # GitHub Actions (keyless)
```

## Cost
One query, 0 bytes scanned; 28 short Gemini calls (14 lines × 2 models, ~150 input tokens each) through the Vertex AI
API: well under one cent of free-trial credit per run.

## Result
Answer key (local): 14 lines → 4 counted in **each**, 6 in **g**, 4 in **ml**; e.g. three 2L bottles = 6000 ml,
1.5 lb = 680.388 g, a baker's dozen = 13, two 400g tins = 800 g. The cloud run reports how many lines each model gets
right; see the report for the day the run finished. Numbers in [`result.json`](result.json).
