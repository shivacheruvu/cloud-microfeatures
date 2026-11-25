# Day 51 · Google Cloud · Post-Thanksgiving feedback digest with typed AI.AGG output

**New platform feature:** **`output_schema` for `AI.AGG`**: structured `STRUCT` output from BigQuery's AI aggregate
function (GA **2026-10-14**, the day after `AI.AGG` itself went GA on 2026-10-13), from the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)
([reference: AI.AGG](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-agg)).
The release-notes page itself could not be fetched in this unattended run; the dates come from
[statmike/bigquery-data-science](https://github.com/statmike/bigquery-data-science) (October 2026 release-notes sweep,
"AI.AGG graduated to GA (2026-10-13) and gained output_schema and max_error_ratio (GA 2026-10-14)") and the syntax
from Google's [`bigquery-ai-ml` skill](https://github.com/google/skills/tree/main/skills/cloud/bigquery-ai-ml)
(`references/ai_agg.md`, updated 2026-11-23).

## What it does
24 shopper comments from Thanksgiving week, six each for produce, bakery, dairy and meat
([`data/comments.csv`](data/comments.csv)). One query groups them by department and asks `AI.AGG` for a typed digest
per group instead of a paragraph:

```sql
SELECT department, COUNT(*) AS comments,
       AI.AGG(comment, "These are shopper comments about one grocery store department ...",
              output_schema => 'top_issue STRING NOT NULL OPTIONS(description = "one of out_of_stock, price, ..."),
                                sentiment STRING NOT NULL, any_out_of_stock BOOL NOT NULL, headline STRING NOT NULL')
         AS digest
FROM UNNEST([...24 comments...])
GROUP BY department
```

Exact numbers come from `COUNT(*)`, not from the model: `AI.AGG` batches its input and summarises summaries, so any
count it states is a reconstruction. The model only labels. The comments are inlined, so nothing is created.

Every comment is hand-labelled (issue, sold-out yes/no, sentiment), and [`feedback_digest.py`](feedback_digest.py)
derives the answer key from those labels: top_issue is the issue raised by the most shoppers (`none` if fewer than two
complain), any_out_of_stock is true if one comment reports a sold-out item, sentiment is the majority. The traps:

| Department | Comments | top_issue | sentiment | any_out_of_stock | Trap |
|---|---|---|---|---|---|
| bakery | 6 | wait_time | negative | false | five ways to say "the line was slow" |
| dairy | 6 | price | negative | false | no stock problems despite the complaints |
| meat | 6 | none | positive | **true** | glowing, except one shopper whose 20 lb turkeys were sold out |
| produce | 6 | out_of_stock | negative | true | six different sold-out items are one issue |

**Must hold** (else the cloud run fails): four departments back, six comments each, every digest a STRUCT with an
allowed top_issue and sentiment, a BOOL any_out_of_stock and a non-empty headline. **Reported:** fields equal to the
answer key (12 = 4 × 3) and the four headlines. A model difference is a warning (a finding about the model, not a
broken run).

## Why it matters
`AI.AGG` already summarised groups of rows, but its answer was a paragraph. With `output_schema` the per-store or
per-department digest comes back as columns: filter departments with a sold-out problem, chart top issues by store,
join to the stock table. Store managers get a one-line headline; the dashboard gets typed fields it can trust to exist.

## How to run
```bash
python features/gcp/day-51-feedback-digest-ai-agg/feedback_digest.py         # answer key, writes result.json
python features/gcp/day-51-feedback-digest-ai-agg/feedback_digest.py --sql   # the BigQuery SQL
pytest -q tests/test_day51_gcp_ai_agg_output_schema.py                       # no credentials needed
bash features/gcp/day-51-feedback-digest-ai-agg/cloud.sh                     # GitHub Actions (keyless)
```

## Cost
One query, 0 bytes scanned; a handful of short Gemini calls on the default model (4 groups of 6 short comments)
through the Vertex AI API: well under one cent of free-trial credit per run.

## Result
Answer key (local): 24 comments, 16 complaints, 6 sold-out mentions; 4 departments, 12 checked fields (table above).
The cloud run reports how many of the 12 fields `AI.AGG` matches and the headlines. Numbers in
[`result.json`](result.json).

**Cloud run (2026-11-24, first run after the merge, PR #166):** `AI.AGG` returned a usable typed STRUCT for all four
departments and **matched 11/12 fields (3/4 departments)**. Bakery (wait_time, negative), dairy (price, negative) and
produce (out_of_stock, negative, six different sold-out items read as one issue) were right. The miss is the planted
trap: for meat it gave `top_issue = out_of_stock` from a single shopper's sold-out turkeys, although the instruction says
`none` when fewer than two shoppers complain; its sentiment (positive) and sold-out flag (true) were right. Headlines:
"Bakery pre-order pickup service was significantly delayed due to understaffing during the holiday rush", "Customers are
highly satisfied with meat quality and staff service despite some turkey size shortages". Lesson: typed output fixes the
shape, not the threshold rule; counting ("fewer than two") is better done in SQL.
