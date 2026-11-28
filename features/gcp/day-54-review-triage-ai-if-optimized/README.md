# Day 54 · Google Cloud · Shopper-review triage with AI.IF in optimized mode

**New platform feature:** **optimized mode for `AI.IF`** (`optimization_mode => 'MINIMIZE_COST'`, GA,
**2026-11-17**). In `MINIMIZE_COST` mode BigQuery answers most rows with a local, distilled model and sends only
the rest to Gemini, which cuts latency and cost; `MAXIMIZE_QUALITY` sends every row to Gemini. From the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) (November 17, 2026: optimized mode
and multi-label for `AI.IF` / `AI.CLASSIFY`; day 47 shipped the multi-label half). The argument name and its two
values come from the [BigQuery DataFrames source](https://github.com/googleapis/python-bigquery-dataframes)
(`bigframes.bigquery.ai.if_(optimization_mode=...)` and its SQL snapshot test, which compiles to
`optimization_mode => 'MINIMIZE_COST'`), because the reference page couldn't be read from the build session. The
upstream [google/skills](https://github.com/google/skills) `ai_if.md` reference doesn't list the argument yet.

## What it does
40 one-line shopper reviews ([`data/reviews.csv`](data/reviews.csv)), 20 of which report a product-quality problem
(moldy strawberries, cracked eggs, a thawed bag of peas, six buns in a pack of eight, oat milk swapped for almond
milk) and 20 of which don't (praise, price, late delivery, rude service, "not a single bad berry"). One query asks the
same question both ways:

```sql
SELECT review_no,
       AI.IF(prompt => (CONCAT('Shopper review of ', review), ' Does this shopper review say the item ...?'),
             optimization_mode => 'MINIMIZE_COST')    AS cost,
       AI.IF(prompt => (...same...), optimization_mode => 'MAXIMIZE_QUALITY') AS quality
FROM UNNEST([...40 reviews...])
```

Nothing is created. [`triage.py`](triage.py) holds the answer key, the SQL and the check. `cloud.sh` tries two call
forms and names the one BigQuery accepts: (1) both modes side by side, (2) `MINIMIZE_COST` only. `max_error_ratio` is
left out: the DataFrames docstring says it isn't supported with `MINIMIZE_COST`.

A local baseline is a generic complaint-word rule (`bad`, `damage`, `spoil`, `mold`, `leak`, `wrong`, `late`,
`rude`, ...), fixed up front and not tuned to these reviews, the kind of rule a triage queue starts with.

**Must hold** (else the cloud run fails): 40 rows; every `MINIMIZE_COST` answer is a BOOL, at most 4 NULLs.
**Reported:** accuracy, precision and recall for each mode, the reviews each got wrong, and how often the two modes
agree. `MINIMIZE_COST` accuracy under 0.85 is a warning.

## Why it matters
Review triage is a bulk yes/no question: thousands of rows a day, most of them easy. Optimized mode is the same
function with one more argument, and it decides per row whether the cheap local model is enough. Running both modes
on the same rows shows what the saving costs in answers before switching a production filter over.

## How to run
```bash
python features/gcp/day-54-review-triage-ai-if-optimized/triage.py          # answer key + baseline, writes result.json
python features/gcp/day-54-review-triage-ai-if-optimized/triage.py --sql 1  # the BigQuery SQL (call form 1)
pytest -q tests/test_day54_gcp_ai_if_optimized.py                            # no credentials needed
bash features/gcp/day-54-review-triage-ai-if-optimized/cloud.sh             # GitHub Actions (keyless)
```

## Cost
One query, 0 bytes scanned; at most 80 short Gemini calls (40 reviews x 2 modes; `MINIMIZE_COST` should send fewer)
through the Vertex AI API: well under one cent of free-trial credit per run. Nothing to clean up.

## Result
Local: 40 reviews, 20 problems in the key. The keyword baseline gets **accuracy 0.525** (precision 0.545, recall 0.3):
it misses "cracked", "thawed", "crushed", "six buns" and flags "late", "rude", "pricey" and "not a single bad berry".
The cloud run reports both AI.IF modes against the key. Numbers in [`result.json`](result.json).

**Cloud run (2026-11-27, first run after the merge):** call form 1 (both modes side by side) was accepted.
`MINIMIZE_COST` got **40 of 40** reviews right (precision 1.0, recall 1.0, no NULLs), and so did `MAXIMIZE_QUALITY`;
**the two modes agreed on all 40**. That includes the traps the keyword rule fell for ("not a single bad berry",
late delivery, rude service) and the ones it missed (thawed peas, crushed chips, six buns in a pack of eight). The
query doesn't report how many rows the distilled model answered locally, so the saving itself isn't measured here.
