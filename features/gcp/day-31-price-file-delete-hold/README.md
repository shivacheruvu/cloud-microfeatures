# Day 31 · Google Cloud · Incident hold on the price-file bucket with hard delete pause

**New platform feature:** **Cloud Storage hard delete pause**, listed in the
[Google Cloud release notes](https://docs.cloud.google.com/release-notes) on **2026-11-04** ("pause hard deletions of
soft-deleted objects in a bucket to allow more time to restore them"). In the JSON API it is
`softDeletePolicy.hardDeletePause` on the bucket: `enabled` (bool) plus a server-set `effectiveTime`. While it is on,
soft-deleted objects are not permanently deleted, whatever their retention says, until the pause is disabled. The
Cloud Storage docs pages didn't describe it yet in this run; the field names come from Google's generated
`storage/v1` API clients (`Bucket.SoftDeletePolicy.HardDeletePause`). `gcloud storage buckets update` has no flag for
it yet, so the run uses the JSON API with the runner's gcloud token.

## What it does
Each store drops a daily shelf-price CSV into a raw bucket with 7-day soft delete. [`price_hold.py`](price_hold.py)
plays out an incident:

1. 7 files land: east / west / north for Monday 11-02 and Tuesday 11-03, plus an unapproved half-price
   `west-draft.csv`.
2. A broken cleanup job deletes 5: all of Monday, east's Tuesday file, and the draft (rightly).
3. Ops **pauses hard deletion** right away, so nothing can age out while the incident is open:

   ```http
   PATCH /storage/v1/b/BUCKET?fields=softDeletePolicy
   {"softDeletePolicy": {"retentionDurationSeconds": "604800", "hardDeletePause": {"enabled": true}}}
   ```
4. Lists the soft-deleted objects (`objects.list?softDeleted=true`), restores the 4 good files from their
   soft-deleted generations (`objects.restore?generation=G`), and leaves the draft soft-deleted.
5. Lifts the pause (`enabled: false`).

## Result (local plan; the cloud run checks each step)
| Step | Files |
|---|---|
| Uploaded | 7 (1,197 bytes) |
| Deleted by the bad cleanup | 5 |
| Live before the restore | 2 (north, west Tuesday) |
| **Restored with the original MD5** | **4** (24 price rows) |
| Left soft-deleted | 1 (`west-draft.csv`) |
| Live after | 6 = every approved price file |

The cloud run checks: the pause reads back `enabled` with an `effectiveTime`; the soft-deleted listing is exactly the
5 deleted files, each with a `hardDeleteTime`; a soft-deleted file can't be read; every restored file's `md5Hash`
equals the original upload; the live listing equals the plan; the pause reads back as lifted.

## Why it matters
Soft delete gives 7 to 90 days to notice a mistake, but incidents run long, and a file deleted on day 1 of a 7-day
window can expire before anyone has worked out which generation to restore. Before this release the only lever was
raising the retention, which only applies to objects deleted after the change. The pause freezes what is already
soft-deleted, for as long as the investigation takes, and is lifted with one call.

## How to run
```bash
python features/gcp/day-31-price-file-delete-hold/price_hold.py      # local plan, writes result.json
pytest -q tests/test_day31_gcp_hard_delete_pause.py                  # plan + cloud runner against a fake JSON API
bash features/gcp/day-31-price-file-delete-hold/cloud.sh             # GitHub Actions (keyless), needs GCP_PROJECT_ID
```

The cloud run creates a short-lived US bucket (uniform access, public access prevention, 7-day soft delete) named
from the Actions run ID, runs the incident, then tears down: lifts the pause, clears soft delete, restores and
deletes anything left so it is gone for good, and deletes the bucket. Leftover `cmf-d31-` buckets from an
interrupted run are torn down first. Bucket names and the project ID are redacted from the log.

## Cloud runs
- 2026-11-04 (after #115): **failed** at the pause PATCH with HTTP 412 (reason cut off in the annotation); teardown
  couldn't delete the bucket (409). Fixed forward: one-line error reasons, a pause-only PATCH retry, and cleanup
  counts in the warning.

## Cost
Seven ~170-byte objects for a few seconds and about 40 JSON API calls: well under a cent.

## Limits of this slice
The run can't wait out a 7-day retention, so it doesn't show a paused object outliving its `hardDeleteTime`; it
checks the pause is set and read back, and that restore works under it. The feature is a day old, so the JSON field
names follow the generated API clients, not a published guide.
