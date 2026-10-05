# Cloud & Data Infra Microfeatures

One small, working data-engineering feature a day for 80 days: **Databricks for days 1–14** (the free trial),
then **Google Cloud for days 15–80**. Each one runs end to end, has a test, and shows one skill.

Live progress: [shivacheruvu.github.io/projects.html](https://shivacheruvu.github.io/projects.html#section-microtools) ·
Plan: [ROADMAP.md](ROADMAP.md)

[![Tests](https://github.com/shivacheruvu/cloud-microfeatures/actions/workflows/ci.yml/badge.svg)](https://github.com/shivacheruvu/cloud-microfeatures/actions/workflows/ci.yml)
[![Secret scan](https://github.com/shivacheruvu/cloud-microfeatures/actions/workflows/secret-scan.yml/badge.svg)](https://github.com/shivacheruvu/cloud-microfeatures/actions/workflows/secret-scan.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Features

<!-- features:start -->
| Day | Platform | Microfeature | Shows | Status |
|---|---|---|---|---|
| — | — | First microfeature ships on day 1 | — | — |
<!-- features:end -->

## Run any of them yourself

Every feature folder has its own README. Each one runs locally with no cloud account (`pytest`), and on your
own Databricks or Google Cloud account using your own credentials:

```bash
cp .env.example .env     # fill in YOUR workspace / project; .env is git-ignored
pip install -r requirements-dev.txt
pytest
```

Nothing in this repo can spend anyone else's money: there are no keys in the code, cloud access in CI uses
GitHub Actions secrets and keyless Workload Identity Federation, and every push is secret-scanned.
See [SECURITY.md](SECURITY.md).

## How it's built

Each day's feature is built by Claude following [CLAUDE.md](CLAUDE.md), merged through a pull request that must
pass the secret scan and tests, and summarised in [`reports/`](reports/).
