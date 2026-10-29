# Day 24 · Google Cloud · Store drop buckets labelled from the moment they exist

**New platform feature:** `--labels` and `--labels-file` on **`gcloud storage buckets create`** (Google Cloud CLI),
listed under Cloud Storage in the [Google Cloud release notes](https://docs.cloud.google.com/release-notes) on
**2026-10-27**. Before, `gcloud storage buckets create` couldn't set labels: a bucket was created bare and labelled
with a second `gcloud storage buckets update --update-labels` call.

## What it does
Every store gets its own raw-drop bucket for point-of-sale exports or price-book files. The store list
([`data/stores.json`](data/stores.json)) is free text ("Ops – East", "East Side #12"), so
[`drop_buckets.py`](drop_buckets.py) first turns each attribute into a valid Cloud Storage label (lowercase ASCII,
digits, `_` and `-`, at most 63 characters, keys starting with a letter) and writes one labels file and one lifecycle
file per store. Then each bucket is created in **one call**:

```bash
gcloud storage buckets create gs://<bucket> --location=US --uniform-bucket-level-access \
  --public-access-prevention --soft-delete-duration=0 \
  --labels-file=east.labels.json --lifecycle-file=east.lifecycle.json
```

```json
{"store": "east", "store_name": "east-side-12", "region": "us-central", "owner": "ops-east",
 "feed": "pos-exports", "cost_center": "cc-4410", "retention_days": "30",
 "program": "cloud-microfeatures", "feature": "day-24", "run": "<run id>"}
```

The lifecycle rule deletes drops after the store's `retention_days`, so the `retention_days` label and the rule
that enforces it are set together.

## Why it matters
Labels are what billing exports, cost reports and "who owns this bucket?" queries group by. With create-then-update,
a script that fails between the two calls (or a person who forgets the second) leaves an unlabelled bucket that no
cost report attributes to anyone. Now the bucket can't exist without its labels.

## How to run
```bash
python features/gcp/day-24-labelled-drop-buckets/drop_buckets.py          # local plan; writes result.json
pytest -q tests/test_day24_gcp_bucket_labels.py
```
The test also runs `cloud.sh` against a fake `gcloud` to check every bucket is created with `--labels-file` and
`--lifecycle-file` in a single call, no `buckets update` is ever made, and every bucket is deleted.

`cloud.sh` (GitHub Actions, keyless) checks `gcloud help storage buckets create` lists `--labels-file` (Google's rule:
check help before using a command; an older SDK fails with a clear error), deletes leftovers from an interrupted run,
creates the 3 buckets, reads each back with `gcloud storage buckets describe --format=json` and fails unless every
label and lifecycle rule equals the local plan. All 3 buckets are deleted on exit.

## Cost
Three empty buckets that live for a few seconds, and a handful of Cloud Storage API calls: $0.

## Result
| Store | Owner | Cost center | Retention | Labels |
|---|---|---|---|---|
| east | ops-east | cc-4410 | 30 days | 9 |
| west | ops-east | cc-4420 | 30 days | 9 |
| hq | pricing | cc-1000 | 90 days | 9 (store name cut to 63 characters) |

3 buckets, 1 create call each, 0 label-update calls; by owner: ops-east 2 buckets (30 days), pricing 1 bucket
(90 days). Numbers in [`result.json`](result.json).
