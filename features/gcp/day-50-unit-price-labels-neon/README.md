# Day 50 · Google Cloud · Unit-price shelf labels from messy pack sizes with gemini-4-neon

**New platform feature:** **the `gemini-4-neon` model in BigQuery generative AI functions** (GA, **2026-11-19**),
from the [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) (November 19, 2026)
([model page](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/4-neon),
[reference: AI.GENERATE](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-generate)).

## What it does
14 Thanksgiving-shop products whose size exists only in the product name ([`data/products.csv`](data/products.csv)):
"Sparkling water 12 x 355 ml cans", "Unsalted butter, 4 x 4 oz sticks", "Heavy cream 1 pint (473 ml)", "Large brown
eggs, one dozen", "Stuffing mix six-pack, 170 g boxes", "Fresh apple cider, 1 gallon jug"... A unit-price label
(price per 100 g, per 100 ml or per item) needs three things per product: how many units are in the pack, the size of
one unit, and its unit. One query asks `AI.GENERATE` for those as typed columns on the new model and, for comparison,
on the default model:

```sql
SELECT product_no,
       AI.GENERATE(CONCAT("Read the pack size from a grocery product name... Product name: ", product),
                   endpoint => 'gemini-4-neon',
                   output_schema => 'pack_count INT64, amount FLOAT64, unit STRING') AS neon,
       AI.GENERATE(..., endpoint => 'gemini-3.5-flash-lite', output_schema => ...) AS baseline
FROM UNNEST([...14 products...])
```

The products are inlined, so nothing is created. [`unit_prices.py`](unit_prices.py) holds the hand-worked answer key,
the SQL, the label arithmetic (oz/lb to grams, fl oz/gallon to millilitres) and the check. The traps are deliberate:
"six-pack" and "dozen" are words, not digits; oz is weight but fl oz is volume; the cream has an imperial and a metric
size; "12 count" rolls have no weight at all.

**Must hold** (else the cloud run fails): 14 rows; every `gemini-4-neon` answer has an empty `status`, a positive whole
`pack_count`, a positive `amount` and a unit from the allowed list (g, kg, oz, lb, ml, l, fl_oz, gal, each).
**Reported:** fields equal to the answer key (42 = 14 × 3), products fully right and unit-price labels right to the
cent, for both models. A `gemini-4-neon` difference is a warning (a finding about the model, not a broken run).

## Why it matters
Unit pricing is how shoppers compare a 12-pack with a 2-litre bottle, and in many places shelf labels must show it. Pack
sizes arrive as free text from suppliers; regexes break on "six-pack", "one dozen" and "(473 ml)". Typed
`output_schema` fields turn the name into numbers SQL can divide by, and checking a new model is a one-word `endpoint`
change against your own answer key. Labels are a first pass for a person to confirm.

## How to run
```bash
python features/gcp/day-50-unit-price-labels-neon/unit_prices.py         # answer key + labels, writes result.json
python features/gcp/day-50-unit-price-labels-neon/unit_prices.py --sql   # the BigQuery SQL
pytest -q tests/test_day50_gcp_gemini_4_neon.py                           # no credentials needed
bash features/gcp/day-50-unit-price-labels-neon/cloud.sh                 # GitHub Actions (keyless)
```

## Cost
One query, 0 bytes scanned; 28 short Gemini calls (14 products × 2 models, ~200 input tokens each) through the Vertex
AI API: well under one cent of free-trial credit per run.

## Result
Answer key (local): 14 products, 42 fields; 4 multipacks; labels per 100 g (7), per 100 ml (5) and per item (2). For
example sparkling water $0.14 / 100 ml, butter $1.17 / 100 g, eggs $0.38 / item, turkey $0.44 / 100 g. The cloud run
reports how many fields, products and labels each model gets right. Numbers in [`result.json`](result.json).

**Cloud run (2026-11-23, first run after the merge, PR #164):** `gemini-4-neon` answered all 14 products with usable
typed sizes and got **42/42 fields right (14/14 products, 14/14 unit-price labels to the cent)**; the default
`gemini-3.5-flash-lite` also got **42/42**. With the rules spelled out in the prompt ("six-pack is 6", "a dozen is 12",
"use the metric size in brackets"), both models read these names correctly; the new model ties the default here.
