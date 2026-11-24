# Day 49 · Databricks · Out-of-stock substitution calls with ai_decide

**New platform feature:** **`ai_decide`** (Beta), listed in the
[Databricks platform release notes, October 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/october)
([reference: ai_decide](https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_decide),
[announcement](https://databricks.com/blog/introducing-aidecide-make-fast-decisions-your-governed-data)). The
release-notes page itself could not be opened from this session (permission timeout), so the exact day was not read;
the manifest uses **2026-10-28**, the day Databricks' own agent skills
([databricks-agent-skills](https://github.com/databricks/databricks-agent-skills), `databricks-ai-functions`) added
it, which is where the syntax below comes from.

## What it does
Twelve Thanksgiving orders have an item out of stock and a proposed substitute
([`data/substitutions.csv`](data/substitutions.csv)): store-brand milk for organic, crunchy peanut butter for creamy,
classic stuffing mix for gluten-free, soy milk for almond, 18 eggs for 12, brioche rolls (egg) for dinner rolls,
dried cranberries for fresh... The picker app needs three answers per pair, and `ai_decide` gives all three in
**one inference per row**, as one VARIANT:

```sql
SELECT sub_no, to_json(ai_decide(state, '{
  "verdict":      {"type":"choice", "instructions":"...use the proposed substitute?",
                   "criteria":{"accept":"...","ask_customer":"...","reject":"..."}},
  "new_allergen": {"type":"noul",   "instructions":"Does the substitute contain any allergen not in ...?"},
  "closeness":    {"type":"score",  "instructions":"How close...?",
                   "criteria":["Different product","Same product, different variety","Practically identical"]}
}')) AS decision
FROM VALUES (1, '{"ordered_item": "Organic whole milk, 1 gal", ...}'), ... AS t(sub_no, state)
```

`state` is a small JSON per pair (names with sizes, prices, both allergen lists). The store's rule is written into the
`choice` criteria and implemented in [`substitutions.py`](substitutions.py) as a local reference: **reject** a
different kind of product or any new allergen; **accept** the same variety with only brand/organic status or size
within 20 % changing and price at most 20 % higher; otherwise **ask the customer**. The rule reproduces the
hand-labelled key on all 12 pairs.

**Must hold** (else the cloud run fails): 12 rows; each has a response and no `error_message`, a `verdict.choice` that
is one of the three labels, a `new_allergen.probability` in [0, 1] and a `closeness.score` in [0, 2]. **Reported:**
verdicts and allergen flags (probability > 0.5) equal to the key, and mean closeness per verdict. A disagreement is a
warning (a finding about the model, not a broken run).

## Why it matters
A substitution is three judgements at once (is it the same thing, is it safe, would the shopper mind). Chaining
`ai_classify`, a yes/no prompt and a scoring prompt costs three model calls and three prompts to keep consistent;
`ai_decide` takes one constant question set and returns typed answers with probabilities, so a picker app can
auto-accept only high-confidence `accept` answers and always route allergen risks to a person.

## How to run
```bash
python features/databricks/day-49-substitution-ai-decide/substitutions.py          # rule vs key, writes result.json
python features/databricks/day-49-substitution-ai-decide/substitutions.py --sql    # the Databricks SQL
pytest -q tests/test_day49_databricks_ai_decide.py                                  # no credentials needed
bash features/databricks/day-49-substitution-ai-decide/cloud.sh                    # GitHub Actions
```
`ai_decide` is Beta: a workspace admin turns it on on the **Previews** page. Needs a serverless (or pro) SQL warehouse.

## Cost
One statement, 12 rows, 12 model calls on the serverless SQL warehouse. Free Edition has a daily compute quota and no
charge; nothing is created.

## Result
Local: 12 substitutions, rule = key on 12/12; verdicts accept 3, ask_customer 5, reject 4; 3 pairs add an allergen
(wheat, soy, egg). The cloud run reports how many verdicts and allergen flags `ai_decide` gets right. Numbers in
[`result.json`](result.json). Status **built**: the Databricks workspace currently refuses compute (see the report).
