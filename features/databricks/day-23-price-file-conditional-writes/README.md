# Day 23 · Databricks · Two stores, one price file, no lost edits: Files API 3.0 conditional writes

**New platform feature:** **Files API 3.0 for Unity Catalog volumes** (Beta): ETags on every file plus conditional
operations (`If-Match`, `If-None-Match`) for uploads, downloads and deletes, and multipart uploads past 5000 MiB.
Announced in the [Databricks release notes, October 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/october#files-api-30-for-unity-catalog-volumes-is-now-in-beta)
on **2026-10-23** ([how-to](https://docs.databricks.com/aws/en/files/files-api),
[upload reference](https://docs.databricks.com/api/files/v3/upload-file)).

## What it does
HQ keeps `shelf_prices.csv` in a Unity Catalog volume; stores edit it. The scenario:

| # | Step | Request | Outcome |
|---|---|---|---|
| 1 | HQ publishes the file | `PUT` + `If-None-Match: *` | ok |
| 2 | HQ publishes again by mistake | `PUT` + `If-None-Match: *` | **rejected** (file exists) |
| 3 | east and north both read it | `GET` (same `ETag`) | ok |
| 4 | east saves an oat-milk markdown (3.49 → 2.99) | `PUT` + `If-Match: <etag>` | ok |
| 5 | north saves an eggs price match from its old copy | `PUT` + `If-Match: <old etag>` | **rejected** |
| 6 | north re-reads, re-applies (4.79 → 4.49), saves | `PUT` + `If-Match: <new etag>` | ok |
| 7 | a delete from a stale copy | `DELETE` + `If-Match: <old etag>` | **rejected** |
| 8 | cleanup | `DELETE` + `If-Match: <current etag>` | ok |

## Why it matters
Before 3.0, a volume upload was last-writer-wins: north's save in step 5 would have silently put oat milk back to
$3.49. With an ETag check, the stale write fails loudly and the store merges instead. It's optimistic concurrency for
plain files (price lists, planograms, config) without a table, a lock service or a warehouse.

## How to run
```bash
python features/databricks/day-23-price-file-conditional-writes/price_file.py --local        # writes result.json
python features/databricks/day-23-price-file-conditional-writes/price_file.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN
pytest -q tests/test_day23_databricks_files_api_conditional.py
```
The local run plays the scenario against an in-memory volume that follows the documented rules. The test also plays
it **over HTTP** through the real Files API 3.0 client against a local server, so the request paths and conditional
headers are checked without cloud access.

`cloud.sh` (GitHub Actions) needs **no SQL warehouse**: it creates a scratch schema and managed volume through the
Unity Catalog REST API, works out the Files API 3.0 regional hostname (workspace ID from the `X-Databricks-Org-Id`
header, region from the metastore summary; never printed), plays the scenario, and fails if any step's outcome or
the final file differs from local. Volume and schema are deleted on exit. If Files API 3.0 isn't turned on (it's a
Beta: a workspace admin enables **Files API V3 with Large File Upload Support** on the Previews page), the run says
so as a warning and the scenario counts as verified locally only.

## Cost
A few REST calls and a 170-byte file; no compute. Fine on Free Edition (if the Beta is offered there).

## Result
3 writes rejected (1 duplicate create, 1 stale upload, 1 stale delete); both store edits kept (P01 $2.99,
P02 $4.49). Last-writer-wins would have lost east's markdown. Full numbers in [`result.json`](result.json).
