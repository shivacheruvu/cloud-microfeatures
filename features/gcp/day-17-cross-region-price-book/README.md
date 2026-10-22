# Day 17 · Google Cloud · Ship the price book to the EU with a cross-region table copy

**New platform feature:** **copy tables across regions** in BigQuery, **generally available 2026-10-15**
([BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes), entry "You can copy tables
across regions"; how-to:
[Copy tables across regions](https://docs.cloud.google.com/bigquery/docs/managing-tables#copy_tables_across_regions)).
A plain `bq cp` (or a copy job from a client library) now works when the source and destination datasets are in
different locations; before, it meant an export to Cloud Storage and a load, or a Data Transfer Service config.
Copy jobs have no query charge, so this stays inside the free-trial credits.

## What it does
The US team builds the weekly shelf price book (36 made-up SKUs in 6 categories, clustered by category) in a US
dataset. The EU analytics team needs it in an EU dataset so their queries run in-region. `cloud.sh`:

1. creates a short-lived dataset in `US` and one in `EU`, and builds `price_book` in the US with a CTAS;
2. **copies it across regions in one command**:
   `bq cp -f PROJECT:mf_day17_us_….price_book PROJECT:mf_day17_eu_….price_book`;
3. reads the EU copy's metadata (`bq show`: location, clustering, description, row count) and runs the same check
   query in both regions: per-category `COUNT(*)`, `SUM(price_cents)` and a row fingerprint
   `BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(t)))`;
4. marks S05 down by 30c **in the US** and runs the check in both regions again.

## Why it matters
Data residency and in-region analytics usually need a second copy of a table in another location. Cross-region copy
makes that one copy job, and the check shows what it is and isn't: the EU table is an exact snapshot (same
fingerprint as the US table at copy time), and it does **not** follow later US edits. For a copy that stays in sync,
use cross-region dataset replication instead; for a periodic hand-off like a weekly price book, a copy is enough.

## How to run
```bash
python features/gcp/day-17-cross-region-price-book/price_book.py --local             # writes result.json
python features/gcp/day-17-cross-region-price-book/price_book.py --sql check P.DS.T  # print the check query
bash features/gcp/day-17-cross-region-price-book/cloud.sh                             # needs GCP_PROJECT_ID + bq auth
pytest -q tests/test_day17_gcp_cross_region_copy.py
```
The cloud run fails if the copy isn't in the EU or has a different row count, if any count or sum differs from the
local price book (before or after the markdown), if the EU fingerprint differs from the US fingerprint at copy time,
or if the EU copy changed with the US markdown. Whether clustering and the description came along is reported in the
notice (not enforced). Both datasets are deleted on exit and their tables expire after an hour anyway.

## Cost
CTAS + UPDATE at the 10 MiB minimum each, four check queries capped at 20 MiB each (a few KB actually read), one
copy job (no query charge; a few KB of cross-region transfer). Well under $0.01 of trial credit.

## Result
| Category | SKUs | Total (cents) |
|---|---|---|
| dairy | 6 | 2936 |
| produce | 6 | 1742 |
| bakery | 6 | 3328 |
| pantry | 6 | 2322 |
| frozen | 6 | 3956 |
| snacks | 6 | 3034 |
| **all** | **36** | **17318** |

After the copy, the US marks S05 (frozen) down by 30c: the US book totals **17288c**, the EU copy still **17318c**.

Full numbers in [`result.json`](result.json).
