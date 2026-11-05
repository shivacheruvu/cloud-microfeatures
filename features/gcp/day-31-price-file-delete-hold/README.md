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
5. Tries to lift the pause (`enabled: false`). **Finding from the first cloud runs:** the service refuses this until a
   minimum duration has passed (HTTP 412 `conditionNotMet`: "Hard Delete Pause can only be disabled after the minimum
   duration has passed. Allowed after: ..."), about 24 hours after the pause took effect, and a bucket with the pause
   on can't be deleted (HTTP 409). So a pause is a commitment of at least a day, and the run records the refusal and
   the minimum it reports instead of failing.

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
equals the original upload; the live listing equals the plan; and the pause either lifts or is refused with the
minimum-duration reason.

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

The cloud run creates a US bucket (uniform access, public access prevention, 7-day soft delete) named from the
Actions run ID and runs the incident. Because of the minimum pause duration the bucket can't be deleted the same day:
teardown empties it and keeps it, and every later run first lifts the pause, clears soft delete and deletes any
earlier `cmf-d31-` bucket whose minimum has passed. Bucket names and the project ID are redacted from the log.

## Cloud runs
- 2026-11-04 (after #115): **failed** at the pause PATCH with HTTP 412 (reason cut off in the annotation); teardown
  couldn't delete the bucket (409). Fixed forward (#116) with one-line error reasons.
- 2026-11-04 (after #116): the reason showed: the pause had been set, the incident ran through the restore, and the
  412 was on **lifting** the pause ("only after the minimum duration ... Allowed after" about 24 h later); the 409
  was "the bucket ... has hard delete pause enabled". Fixed forward (#117): the refusal is an expected outcome, the
  bucket is kept and deleted by a later run. (#116's teardown also restored and re-deleted soft-deleted objects in
  rounds, which only added generations while soft delete stayed on; removed.)
- 2026-11-04 (after #117): **ran.** Every step matched the plan; lifting the pause was refused until 24.0 h after it
  took effect, and the bucket is kept for a later run to delete.

## Cost
Seven ~170-byte objects kept a day or two under the pause (plus a few hundred tiny generations from #116's teardown
in the first two buckets) and about 40 JSON API calls a run: well under a cent.

## Limits of this slice
The run can't wait out a 7-day retention, so it doesn't show a paused object outliving its `hardDeleteTime`; it
checks the pause is set and read back, that restore works under it, and the minimum duration before it can be lifted. The feature is a day old, so the JSON field
names follow the generated API clients, not a published guide.
