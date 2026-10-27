# Day 22 · Google Cloud · Did every store's export land? `OBJ.LIST` over the drop bucket

**New platform feature:** the BigQuery **`OBJ.LIST`** function (GA), which "returns a table of metadata and
`ObjectRef` values for files stored in Cloud Storage" without a persistent object table. Announced in the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) on **2026-09-30**
([reference](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/objectref_functions#objlist),
[guide](https://docs.cloud.google.com/bigquery/docs/work-with-objectref)).

## What it does
Each store's point-of-sale system drops one CSV a day into `exports/<store>_<YYYY-MM-DD>.csv`. One BigQuery query
lists the folder and checks it against the expected store × day calendar:

```sql
WITH listed AS (
  SELECT REGEXP_EXTRACT(uri, r'/exports/([a-z]+)_\d{4}-\d{2}-\d{2}\.csv$') AS store,
         SAFE.PARSE_DATE('%F', REGEXP_EXTRACT(uri, r'_(\d{4}-\d{2}-\d{2})\.csv$')) AS export_date, size
  FROM OBJ.LIST('gs://<bucket>/exports/*')
), expected AS (...)          -- 3 stores x GENERATE_DATE_ARRAY over 3 days
SELECT store, export_date, CASE WHEN size IS NULL THEN 'missing' WHEN size = 0 THEN 'empty' ELSE 'ok' END ...
```

Every expected export comes back `ok`, `empty` (a zero-byte file: the export job ran but wrote nothing) or `missing`.
Files that don't match the naming pattern are ignored. Full SQL in [`audit.sql`](audit.sql).

## Why it matters
"Did last night's files arrive?" used to mean an object table (a connection, a dataset object to maintain) or a
script outside BigQuery. With `OBJ.LIST` it's one ad-hoc query using the caller's own access, and the answer can be
joined straight to the calendar, the store list or the load jobs.

## How to run
```bash
python features/gcp/day-22-export-drop-audit/export_audit.py                 # local audit of data/exports; writes result.json
python features/gcp/day-22-export-drop-audit/export_audit.py --sql BUCKET    # print the BigQuery SQL
pytest -q tests/test_day22_gcp_obj_list.py
```
`cloud.sh` (GitHub Actions, keyless) creates a short-lived bucket (US, uniform access, public access prevented, no
soft delete), uploads the 8 files from [`data/exports/`](data/exports), runs the query with a 20 MiB
bytes-billed cap and fails if any row differs from the local audit. The bucket is deleted on exit.

## Cost
About 1.5 KB stored for a minute and one small query: effectively $0 on the free-trial credits.

## Result
| Store | 2026-10-23 | 2026-10-24 | 2026-10-25 |
|---|---|---|---|
| east | ok (179 B) | **empty** (0 B) | ok (150 B) |
| north | ok (276 B) | ok (209 B) | ok (272 B) |
| south | ok (261 B) | ok (151 B) | **missing** |

8 files (1,498 bytes) listed for 9 expected exports: 7 ok, 1 empty, 1 missing; only north is complete.
Numbers in [`result.json`](result.json).
