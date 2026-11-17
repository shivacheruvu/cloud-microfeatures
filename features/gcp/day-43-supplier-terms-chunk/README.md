# Day 43 · Google Cloud · Supplier terms PDF to search-ready chunks with AI.CHUNK_DOCUMENT

**New platform feature:** **`AI.CHUNK_DOCUMENT`** (GA, **2026-10-21**): a BigQuery table-valued function that sends
a document (a Cloud Storage URI) through the Document AI layout parser and returns it as text chunks, one row each,
with the pages each chunk came from, ready for embeddings and retrieval (RAG). `chunk_size` sets how big a chunk may
grow. From the [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) (October 21, 2026)
([reference](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-chunk-document),
[tutorial](https://docs.cloud.google.com/bigquery/docs/rag-pipeline-pdf)).

## What it does
Day 42 read the Harbor Farms weekly price *table*. The same supplier also sends its **trading terms** as running text
on two pages: ordering, delivery, prices, returns (page 1), recalls, cold chain, payment, shortage claims (page 2).
Store clerks ask questions like "how long do we have to return spoiled yogurt?" or "what's the warmest milk we can
accept?". To answer them with retrieval, the PDF first has to become chunks that keep each term intact and know their
page.

[`supplier_terms.py`](supplier_terms.py) writes that PDF byte for byte ([`data/harbor_farms_terms.pdf`](data/harbor_farms_terms.pdf),
no PDF library), so the eight terms that answer eight clerk questions, and their pages, are known exactly
(`6 cases per SKU`, `Tuesday and Friday before 7:00 AM`, `held for 14 days`, `within 48 hours of delivery`,
`within 2 hours of a recall notice`, `41 F or below`, `take a 2% discount`, `credit memo within 5 business days`).
In the cloud one query chunks it at two sizes, to show what the knob does:

```sql
SELECT 100 AS requested_size, c.*
FROM AI.CHUNK_DOCUMENT('gs://<bucket>/terms/harbor_farms_terms.pdf', chunk_size => 100) AS c
UNION ALL
SELECT 500 AS requested_size, c.*
FROM AI.CHUNK_DOCUMENT('gs://<bucket>/terms/harbor_farms_terms.pdf', chunk_size => 500) AS c
```

No connection: the caller's credentials (the keyless GitHub Actions service account) read the object and call the
layout parser through the `bq-created-layout-parser` processor that day 42 created.

**Must hold** (else the cloud run fails): chunks for both sizes, no chunk with an error column set, no empty chunk,
chunks span pages 1 and 2 and nothing else, every one of the 8 terms present in the chunks of each size, and
`chunk_size 100` gives at least as many chunks as `500`. **Reported:** chunks per size, longest chunk, the columns
that came back, and per size how many terms sit **whole inside one chunk whose page range holds the term's page**
(what retrieval needs to quote the answer). Fewer than 8 is a warning: a finding about where the chunker cut, not a
broken run.

## Why it matters
Retrieval is only as good as its chunks: a term cut in half ("41 F" | "or below") can't be found or quoted. With
`AI.CHUNK_DOCUMENT` the chunking runs in the warehouse, next to `AI.EMBED` / `VECTOR_SEARCH`, and the result can be
checked in SQL against what the document says before anything is indexed.

## How to run
```bash
python features/gcp/day-43-supplier-terms-chunk/supplier_terms.py         # writes the PDF + result.json (answer key)
python features/gcp/day-43-supplier-terms-chunk/supplier_terms.py --sql gs://BUCKET/terms/harbor_farms_terms.pdf
pytest -q tests/test_day43_gcp_chunk_document.py                           # no credentials needed
bash features/gcp/day-43-supplier-terms-chunk/cloud.sh                     # GitHub Actions (keyless)
```
`cloud.sh` makes sure the Document AI API is on, creates a short-lived bucket (US, uniform access, public access
prevented, no soft delete), uploads the PDF, runs the query, checks it, and deletes the bucket on exit.

## Cost
Two chunking calls over a 2-page PDF: 4 Document AI layout-parser pages per run (about a cent per page or less) and
a query that scans 0 bytes. Nothing is kept except the existing processor (no idle fee).

## Result
Answer key (local): 2 pages, 3,760-byte PDF, 8 sections and 8 terms (4 per page), 171 + 140 words, 8 clerk questions
mapped to their terms. Numbers in [`result.json`](result.json).

**Cloud run (2026-11-16, first run after the merge): passed.** `AI.CHUNK_DOCUMENT` returned **4 chunks at
`chunk_size` 100** (longest 522 characters) and **1 chunk at 500** (1,635 characters: the whole two-page document);
all 8 terms present at both sizes and **8/8 sit whole in one chunk whose page range holds their page** at both
sizes. Besides `chunk_id`, `content`, `start_page` and `end_page`, each row carries the source object's
`ObjectRef`-style columns (`uri`, `ref`, `content_type`, `size`, `md5_hash`, `generation`, `updated`, `metadata`)
and a `status`. Reading: even at 100 no term was cut, and the 4 chunks average about 400 characters, so the chunker keeps
layout units together rather than slicing text at a fixed length.
