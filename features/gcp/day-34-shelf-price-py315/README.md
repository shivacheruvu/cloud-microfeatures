# Day 34 · Google Cloud · Shelf prices and basket totals on the Python 3.15 runtime

**New platform feature:** **Python 3.15 runtime** (`python315`, Preview), in the
[Cloud Run functions release notes](https://docs.cloud.google.com/functions/docs/release-notes) and the
[Google Cloud release notes](https://docs.cloud.google.com/release-notes) on **2026-11-02**: "Support for the Python
3.15 runtime is in Preview." The runtime ships as Google-maintained base images
([runtime base images](https://docs.cloud.google.com/run/docs/configuring/services/runtime-base-images)), e.g.
`us-central1-docker.pkg.dev/serverless-runtimes/google-24-full/runtimes/python315`.

## What it does
A small slice: run real Python 3.15 code on Google's own 3.15 runtime image, as a Cloud Run job (no build step, no
Artifact Registry repo of our own). [`job315.py`](job315.py) is a grocery pricing step written with four Python 3.15
features, so it can't even be parsed by Python 3.14 or older:

| Python 3.15 feature | Used for |
|---|---|
| PEP 798 unpacking in comprehensions | `{**layers[name] for name in stack}` resolves each store's shelf prices through chain → region → store price layers (later wins); `[*feed for feed in feeds]` flattens two promo feeds |
| PEP 814 built-in `frozendict` | `{frozendict(r) for r in raw}` collapses duplicate promo rules even when the feeds list keys in a different order (8 → 6), and each rule is a dict key for hit counts |
| PEP 810 `lazy import statistics` | the module loads only when the median basket is computed (the job checks it wasn't in `sys.modules` before) |
| PEP 686 UTF-8 mode by default | the receipt file (Jalapeño, Crème fraîche, Café molido) is written with no `encoding=` and must read back as UTF-8 |

[`rollup.py`](rollup.py) is the same computation in plain Python 3.12 (the local reference, writes `result.json`).
[`cloud_run.py`](cloud_run.py) creates the job on the `python315` image (1 vCPU, 512 MiB, no retries) with
`--command=python3`, sends the code and the fixture ([`data.json`](data.json)) as base64 env vars, executes it once,
reads the `DAY34_RESULT` line from Cloud Logging, checks it equals the reference **and** reports Python 3.15.x with
`utf8_mode=1`, a built-in `frozendict` and a deferred lazy import, then deletes the job. If the image's interpreter
isn't called `python3`, it retries once with `python`.

## Result (local reference; the cloud job must match it exactly)
| Store | Baskets | Gross | Promo | Net |
|---|---|---|---|---|
| S1 (north + store layer) | 2 | $29.59 | $3.09 | $26.50 |
| S2 (north) | 2 | $32.93 | $3.13 | $29.80 |
| S3 (south + store layer) | 3 | $53.78 | $4.83 | $48.95 |
| S4 (south + store layer) | 3 | $52.76 | $6.13 | $46.63 |
| **All** | **10** | **$169.06** | **$17.18** | **$151.88** |

27 lines, 48 units; promo rules 8 raw → 6 distinct; median basket $14.345. Locally, `job315.py` under Python
3.15.0b4 (uv) printed the same numbers with `utf8_mode=1`, `frozendict` built in and the lazy import deferred.

**Cloud run (2026-11-07, right after the merge):** matched local exactly. The `python315` image ran **Python 3.15.0
final** with `--command=python3` (no fallback needed) under the default job identity; `utf8_mode=1`, built-in
`frozendict`, lazy import deferred; 129 s end to end including job creation, image pull and the log read; job deleted.

## Why it matters
Python 3.15's comprehension unpacking and `frozendict` make layered config merges and rule dedup one-liners, and
UTF-8-by-default ends the "works on my laptop, mojibake in the container" class of bugs for product names. Running on
Google's runtime base image (rather than a Docker Hub image) means the interpreter gets Google's automatic security
patches when used for services/functions. Being Preview, it's for trying code before the GA runtime, not production.

## How to run
```bash
python features/gcp/day-34-shelf-price-py315/rollup.py              # Python 3.12 reference, writes result.json
python3.15 features/gcp/day-34-shelf-price-py315/job315.py features/gcp/day-34-shelf-price-py315/data.json
pytest -q tests/test_day34_gcp_python315_runtime.py                 # reference, 3.15 syntax checks, fake gcloud
bash features/gcp/day-34-shelf-price-py315/cloud.sh                 # needs GCP_PROJECT_ID (GitHub Actions, keyless)
```
(`uv python install 3.15` gives a local 3.15 if you want to run the job code; the test runs it when present.)

## Cost
One Cloud Run job task of a few seconds (1 vCPU, 512 MiB) plus a few log reads: inside the Cloud Run free tier,
effectively $0. The job is deleted on exit; leftover `cmf-d34-*` jobs are deleted first.
