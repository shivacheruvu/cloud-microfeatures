# Day 42 · Google Cloud · Supplier PDF price sheet to rows with AI.PARSE_DOCUMENT

**New platform feature:** **`AI.PARSE_DOCUMENT`** (GA, **2026-11-02**): a scalar BigQuery function that sends a
document (a Cloud Storage URI or an `ObjectRef`) to the Document AI layout parser and returns its layout as JSON:
title, heading, text, table (as HTML), list item ... blocks in reading order, with page numbers and bounding boxes.
From the [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) (November 2, 2026)
([reference](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-parse-document)).

## What it does
A dairy supplier sends a one-page PDF price sheet each week: six SKUs with product, case pack and case cost
([`data/harbor_farms_w46.pdf`](data/harbor_farms_w46.pdf)). Someone retypes it into the cost file.
[`supplier_sheet.py`](supplier_sheet.py) writes that PDF byte for byte (Helvetica text and a ruled grid, no PDF
library), so the answer key is exactly what's printed on the page. In the cloud one query parses it:

```sql
WITH parsed AS (
  SELECT AI.PARSE_DOCUMENT('gs://<bucket>/supplier/harbor_farms_w46.pdf',
                           options => JSON '{"schema": "layout-v1"}') AS doc
)
SELECT JSON_VALUE(doc, '$.metadata.schema') AS schema_used,
       TO_JSON_STRING(JSON_QUERY(doc, '$.errors')) AS errors,
       ARRAY_LENGTH(JSON_QUERY_ARRAY(doc, '$.blocks')) AS block_count,
       TO_JSON_STRING(ARRAY(SELECT AS STRUCT INT64(b.id) AS id, JSON_VALUE(b.type) AS type,
                                   INT64(b.page_number) AS page, JSON_VALUE(b.content) AS content
                            FROM UNNEST(JSON_QUERY_ARRAY(doc, '$.blocks')) AS b WITH OFFSET AS o ORDER BY o)) AS blocks
FROM parsed
```

No connection is needed: the caller's credentials (the keyless GitHub Actions service account) read the object and
call the layout parser. On the first run BigQuery creates one processor, `bq-created-layout-parser`, in the project.

**Must hold** (else the cloud run fails): schema `layout-v1`, no `errors`, at least one block, every block on page 1,
the sheet's title found, and all 6 SKUs and all 6 case costs present in the parsed text. **Reported:** block types,
whether a table block came back, and how many SKU rows are rebuilt with the right case cost (from the table's HTML
rows, or from the text following each SKU). Fewer than 6 rebuilt is a warning: a finding about the layout the parser
returned, not a broken run.

## Why it matters
Supplier price sheets, spec sheets and invoices arrive as PDFs. With `AI.PARSE_DOCUMENT` the parse is one SQL
expression next to the cost table, not a separate OCR service, and the table comes back as HTML rows you can check
against what you expected (here: every SKU and every cost must be found, or nothing gets loaded).

## How to run
```bash
python features/gcp/day-42-supplier-sheet-parse/supplier_sheet.py           # writes the PDF + result.json (answer key)
python features/gcp/day-42-supplier-sheet-parse/supplier_sheet.py --sql gs://BUCKET/supplier/harbor_farms_w46.pdf
pytest -q tests/test_day42_gcp_parse_document.py                             # no credentials needed
bash features/gcp/day-42-supplier-sheet-parse/cloud.sh                       # GitHub Actions (keyless)
```
`cloud.sh` makes sure the Document AI API is enabled, creates a short-lived bucket (US, uniform access, public
access prevented, no soft delete), uploads the PDF, runs the query, checks it, and deletes the bucket on exit.

## Cost
One Document AI layout parser page per run (Layout Parser pricing, about one cent per page or less) plus a query
that scans 0 bytes. The processor has no idle fee and is kept so later runs reuse it; the bucket is deleted.

## Result
Answer key (local): 1 page, 2,611-byte PDF, 6 SKUs, case costs totalling **$160.75**, lowest unit cost **HF-3001**
(large eggs, $2.49 a dozen).

**Cloud run (2026-11-15, first run after the merge):** the Document AI API was enabled by the script, then
`AI.PARSE_DOCUMENT` returned **5 layout-v1 blocks: 2 headings (the sheet title came back as a heading, not a
`title`), 1 table, 2 text**; title, all 6 SKUs and all 6 case costs found, and the table came back as HTML whose rows
rebuild **6/6 SKUs with the right case cost**. Numbers in [`result.json`](result.json).
