# Day 28 · Google Cloud · Bilingual shelf tags with AI.TRANSLATE, and a check that the prices survive

**New platform feature:** **`AI.TRANSLATE`** (GA): a BigQuery SQL function that translates text between languages
through the Cloud Translation API, either standard Neural Machine Translation (`nmt`, the default) or the Translation
LLM (`model => 'translation-llm'`), with no remote model or connection to create first. Announced in the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes#October_22_2026) on
**2026-10-22** ([reference: The AI.TRANSLATE function](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-translate)).

```sql
AI.TRANSLATE(content, target_language [, source_language => ...] [, model => ...] [, return_details => ...])
```

## What it does
A store in a bilingual neighbourhood prints each shelf tag in English and Spanish. One query translates 12 tags
([`data/shelf_tags.csv`](data/shelf_tags.csv)), inlined in the SQL so nothing is created:

```sql
SELECT sku, tag,
       TO_JSON_STRING(AI.TRANSLATE(tag, 'es', source_language => 'en', return_details => TRUE)) AS es
FROM UNNEST([STRUCT("P01" AS sku, "Whole milk 1 gal $4.29" AS tag), ...])
```

`return_details => TRUE` returns a `STRUCT` with `result` and `status` (`"OK"` or the API error), so a failed row is
an explained row, not a silent `NULL`.

A translated tag is only usable if its numbers didn't move. [`shelf_tags.py`](shelf_tags.py) checks every tag:

- **numbers kept**: the digit groups before and after are the same (`$0.59` → `0,59 $` passes; `2 for $5` → `2 por $6` doesn't)
- **product word**: the Spanish product word is there (`leche`, `huevo`, `plátano`/`banana`, ...)
- **changed**: the text was actually translated

The run fails only if the output is broken (a missing row, a `NULL` result, a status that isn't OK); a tag that fails
a check is a warning listing the tag for a human to fix.

## Result
Local (hand-written Spanish reference, so the checker itself is verified): 12 of 12 numbers kept, product word, changed.
Cloud: see the day's report; the run prints the counts and the first translations as a `::notice`.

## Why it matters
Translation was a remote model plus a connection (`ML.TRANSLATE`); now it's one function call next to the data, and a
cheap deterministic check catches the one error that would cost money at the shelf: a changed price.

## How to run
```bash
python features/gcp/day-28-bilingual-shelf-tags/shelf_tags.py          # local, writes result.json
pytest -q tests/test_day28_gcp_ai_translate.py
bash features/gcp/day-28-bilingual-shelf-tags/cloud.sh                 # needs GCP keyless sign-in (CI)
```

`cloud.sh` enables the Cloud Translation API if it's off (free to enable), runs the query in the `US` multi-region
with a 20 MiB bytes-billed cap, and checks the output.

## Cost
About 400 characters of NMT per run (Cloud Translation's first 500,000 characters a month are free; otherwise about
$20 per million characters, so under a cent) and a 0-byte BigQuery query. Nothing is stored.
