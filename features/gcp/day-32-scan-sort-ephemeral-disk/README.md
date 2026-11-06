# Day 32 · Google Cloud · Sort a 6-million-line scan export on a Cloud Run ephemeral disk

**New platform feature:** **Cloud Run ephemeral disk** (GA), in the
[Google Cloud release notes](https://docs.cloud.google.com/release-notes) on **2026-11-04**: a disk volume that lives
only as long as a Cloud Run service, job or worker pool instance
([jobs guide](https://docs.cloud.google.com/run/docs/configuring/jobs/ephemeral-disk)). Before this, a job's scratch
space was the in-memory filesystem, which counts against the instance's memory limit.

## What it does
[`spill_sort.py`](spill_sort.py) is both the local reference and the job. The cloud run creates one Cloud Run job
(public `python:3.12-slim` image, 1 vCPU, 512 MiB, no retries) with

```bash
--add-volume=name=spill,type=ephemeral-disk,size=1Gi --add-volume-mount=volume=spill,mount-path=/mnt/spill
```

and executes it once. The task generates a deterministic 6,000,000-line register scan export
(`sku,store,qty,cents`; 400 SKUs, 12 stores, a skewed SKU mix), sorts it by SKU with an **external merge sort**: six
1-million-line runs sorted in memory and written to `/mnt/spill`, then a streaming k-way merge (`heapq.merge`) of the
six files rolls the lines up per SKU. Run files are removed after the merge. The task prints one `DAY32_RESULT` JSON
line; [`cloud_run.py`](cloud_run.py) reads it from Cloud Logging, checks it equals the local reference and that the
mount really was a ~1 GiB ext4 disk left empty, then deletes the job.

Locally the reference skips the sort entirely (a straight dictionary over the same lines), so a match proves the
spill-and-merge path lost or double-counted nothing.

## Result (local reference; the cloud job must match it exactly)
| Metric | Value |
|---|---|
| Scan lines | 6,000,000 |
| Sorted runs spilled to disk | 6 (1,000,000 lines each) |
| Bytes spilled / merged | 97,659,429 / 97,659,429 |
| SKUs | 400 |
| Units | 14,999,999 |
| Revenue | $112,278,835.50 |
| Per-SKU checksum (sha256, first 16) | `92ddc49fcdb05d50` |

Top SKUs by units: P0000 750,731 · P0001 310,319 · P0002 239,210 · P0003 200,902 · P0004 177,488.
A local run of the job code peaked at 88 MiB RSS while spilling 98 MB, so the runs live on disk, not in memory.

**Cloud run (2026-11-05, after the fix below):** matched local exactly. The mount was a 0.94 GiB ext4 disk (1 GiB
requested, less filesystem overhead), peak RSS 92 MiB of the 512 MiB limit, sort 27.3 s inside the task, 81 s end to
end including job creation and log read, job deleted.

## Why it matters
Sorting, deduplicating or joining an export bigger than a job's memory needs scratch space. With the in-memory
filesystem, every spilled byte also counts against the memory limit, so the job needs a bigger (pricier) memory
setting or fails with out-of-memory. An ephemeral disk gives up to 100 GiB of scratch per volume that is wiped when the
task ends, with no bucket to clean up.

## How to run
```bash
python features/gcp/day-32-scan-sort-ephemeral-disk/spill_sort.py     # local reference, writes result.json (~10 s)
pytest -q tests/test_day32_gcp_ephemeral_disk.py                      # external sort vs reference, fake gcloud
bash features/gcp/day-32-scan-sort-ephemeral-disk/cloud.sh             # needs GCP_PROJECT_ID (GitHub Actions, keyless)
```

The cloud run enables the Cloud Run and Logging APIs if needed, deletes leftover `cmf-d32-*` jobs, creates the job
(falls back to `gcloud beta` on an older gcloud, and to the signed-in service account as the job's identity if the
default compute service account can't be used), executes it with `--wait`, and deletes the job on exit.

## Cost
One task of about a minute at 1 vCPU / 512 MiB plus 1 GiB of ephemeral disk for that minute: inside the Cloud Run free
tier (well under a cent otherwise). A few Cloud Logging reads. Nothing is kept.

## Fix forward (2026-11-05)
The first cloud run (after #120) matched every number but failed the "nothing left on the disk" check: it counted
`lost+found`, which every freshly formatted ext4 disk has. The check now counts only `run-*` files and reports other
entries separately.

## Limits of this slice
One task, one 1 GiB volume, Python's own sort. The regional ephemeral-disk quota starts at 0 and is granted (100 GiB)
on first deploy; this slice doesn't test larger disks, parallel tasks or the 10-second shutdown window.
