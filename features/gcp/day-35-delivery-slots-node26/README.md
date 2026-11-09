# Day 35 · Google Cloud · Delivery promises across the end of DST, on the Node.js 26 runtime

**New platform feature:** **Node.js 26 runtime** (`nodejs26`, Preview), in the
[Cloud Run release notes](https://docs.cloud.google.com/run/docs/release-notes) and the
[Cloud Run functions release notes](https://docs.cloud.google.com/functions/docs/release-notes) on **2026-07-27**:
"Support for the Node.js 26 runtime is in Preview." It ships as Google-maintained
[runtime base images](https://docs.cloud.google.com/run/docs/configuring/services/runtime-base-images) on the
`google-24` (default) and `google-24-full` stacks, e.g.
`us-central1-docker.pkg.dev/serverless-runtimes/google-24/runtimes/nodejs26`.

## What it does
A small slice: run real Node.js 26 code on Google's own Node 26 runtime image as a Cloud Run job (no build step, no
Artifact Registry repo of our own), on a grocery problem that bites once a year: **online orders placed around the end
of daylight saving time** (Sunday 2026-11-01, 02:00 local) at four stores in Chicago, Denver, Phoenix (no DST) and New
York. [`job26.js`](job26.js) uses:

| Feature | In Node.js 26 on the image? | Used for |
|---|---|---|
| `RegExp.escape` | yes (required) | finds catalog names like `M&M's (Peanut)`, `Half & Half 1.5 qt`, `Kombucha [Ginger]` literally in order lines (unescaped, `(Peanut)` is a group, `.` matches `x`, `[Ginger]` is a character class) |
| `Uint8Array.fromBase64` + `TextDecoder` | yes (required) | decodes the order data from the job's env var with UTF-8 names (Jalapeño, Crème fraîche) intact |
| `Temporal` | **not by default** (first cloud run); tried behind V8's `--harmony-temporal` flag | exact-hour promises (`ZonedDateTime#add({hours})`), next-day pickups that keep the wall clock (`add({days: 1})`), `hoursInDay`, the repeated 01:00 hour |
| `Intl.DateTimeFormat` | yes | the same time-zone maths without Temporal (and a cross-check of Temporal when it is there) |
| `Math.sumPrecise` | **no** (first cloud run) | used when present; otherwise the order-line total is summed exactly in integer cents ($85.13; a plain `reduce` gives 85.13000000000001) |

**Fix forward (same day).** The first cloud run (right after PR #128) ran Node.js 26 on the image, and
`RegExp.escape` and `Uint8Array.fromBase64` worked, but there was **no `Temporal` global and no `Math.sumPrecise`**,
so it failed. The job now runs as `node --harmony-temporal` (falling back to plain `node` if the flag is rejected),
uses Temporal when it exists and `Intl.DateTimeFormat` otherwise, and reports which engine ran, whether the two agree,
and whether the flag exposes Temporal. On local Node 22 both paths give exactly the reference results (the test runs
both).

[`slots.py`](slots.py) is the same computation in Python with `zoneinfo`, `math.fsum` and `re.escape` (the local
reference, writes `result.json`). [`cloud_run.py`](cloud_run.py) creates the job on the `nodejs26` image (1 vCPU,
512 MiB, no retries) with `--command=node --args=--harmony-temporal,-e,eval(atob(process.env.DAY35_CODE))`, sends the code and the
fixture ([`data.json`](data.json)) as base64 env vars, executes it once, reads the `DAY35_RESULT` line from Cloud
Logging, checks it equals the reference **and** reports Node.js 26.x with `RegExp.escape` and `Uint8Array.fromBase64`
built in, no errors, and Temporal agreeing with Intl when both ran, then deletes the job. If a task fails it retries
without the flag, then on the `google-24-full` stack.

## Result (local reference; the cloud job must match it exactly)
| Store | Zone | Hours in 2026-11-01 | Restock shift Sat 22:00 → Sun 06:00 |
|---|---|---|---|
| S1 | America/Chicago | 25 | 9 h |
| S2 | America/Denver | 25 | 9 h |
| S3 | America/Phoenix | 24 | 8 h |
| S4 | America/New_York | 25 | 9 h |

| Order | Placed | Promise | Deliver by (exact) | Wall-clock maths says |
|---|---|---|---|---|
| O02 (S1) | 00:40 −05:00 | 2 h | **01:40 −06:00** | 02:40 (an hour late) |
| O03 (S1) | 01:30 −05:00 (ambiguous → earlier) | 1 h | **01:30 −06:00** | 02:30 |
| O05 (S2) | 00:05 −06:00 | 4 h | **03:05 −07:00** | 04:05 |
| O07 (S3) | 00:40 −07:00 | 2 h | 02:40 −07:00 | 02:40 (Phoenix is right) |

- **7 of 10** promises are an hour off if computed on the wall clock; **5** fall due in the repeated 01:00 hour.
- Next-day pickups placed before 02:00 on Nov 1 (or late on Oct 31) are 25 h away, the rest 24 h.
- Catalog name matches (escaped vs unescaped): M&M's (Peanut) 2 vs 1, Half & Half 1.5 qt 1 vs 2, Dr. Pepper 2L
  2 vs 2, Kombucha [Ginger] 2 vs 1.

Locally (Node 22) the job's time-zone and total figures equal the reference on both the Intl and the Temporal path;
only the name matches need Node 26's `RegExp.escape`, so they are checked in the cloud.

**Cloud run (2026-11-08, after the fix in PR #129):** matched local exactly. The `nodejs26` image (`google-24`) ran
**Node.js 26.12.1, V8 14.6**; `node --harmony-temporal` was accepted but still exposes **no Temporal** (V8 built
without it), so the time zones ran on `Intl.DateTimeFormat` and the total in integer cents; `RegExp.escape` and
`Uint8Array.fromBase64` built in; 51 s end to end including job creation, image pull and the log read; job deleted.
The first run (after PR #128) had failed because it assumed Temporal and `Math.sumPrecise`.

## Why it matters
Exact-time arithmetic (Temporal, or Intl until Node enables it) ends the classic "delivery promised for 02:40 when
it's really 01:40" bug: elapsed-time and wall-clock arithmetic are different calls, ambiguous times have an explicit
rule, and a day can be 25 hours long. `RegExp.escape` and `Uint8Array.fromBase64` remove two hand-rolled helpers from
catalog and data-loading code. It also shows what "Node.js 26" does and doesn't give you yet on this runtime. Running on Google's runtime base
image means Google patches the Node.js underneath when it is used for services/functions with automatic updates.
Being Preview, this is for trying code before the GA runtime, not production.

## How to run
```bash
python features/gcp/day-35-delivery-slots-node26/slots.py           # Python reference, writes result.json
DAY35_CODE=$(base64 -w0 job26.js) DAY35_DATA=$(base64 -w0 data.json) node -e 'eval(atob(process.env.DAY35_CODE))'
pytest -q tests/test_day35_gcp_node26_runtime.py                     # reference, feature checks, fake gcloud
bash features/gcp/day-35-delivery-slots-node26/cloud.sh              # needs GCP_PROJECT_ID (GitHub Actions, keyless)
```

## Cost
One Cloud Run job task of a few seconds (1 vCPU, 512 MiB) plus a few log reads: inside the Cloud Run free tier,
effectively $0. The job is deleted on exit; leftover `cmf-d35-*` jobs are deleted first.
