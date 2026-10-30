# Day 25 · Databricks · Personal-data scan scope as code: the Data Classification API

**New platform feature:** **Data Classification API** (GA): a REST API (and Terraform resource) for the per-catalog
Data Classification config, `catalogs/{catalog}/config`, with `included_schemas` or `excluded_schemas`. Creating the
config turns classification on for the catalog; deleting it turns it off. Announced in the
[Databricks release notes, October 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/october)
on **2026-10-14** ([API reference](https://docs.databricks.com/api/workspace/dataclassification)).
Auto-tagging (`auto_tag_configs`) is still Public Preview and isn't used here.

## What it does
The grocery catalog ([`data/catalog.json`](data/catalog.json)) has five schemas: `loyalty`, `pos`, `ecommerce`,
`pricing`, `sandbox`. The scan scope lives in [`data/policy.json`](data/policy.json) as three versions, applied in
order by a small reconciler that reads the current config and plans **one** call:

| Step | Call | Outcome |
|---|---|---|
| read before any change | `GET` | absent |
| apply v1 (`loyalty`, `pos`) | `POST …/catalogs/c/config` | ok |
| create v1 again | `POST` | **rejected** (a catalog has at most one config) |
| apply v1 again | none (plan says no-op) | ok |
| apply v2 (+ `ecommerce`) | `PATCH ?update_mask=included_schemas` | ok |
| apply v3 (everything except `sandbox`) | `PATCH ?update_mask=included_schemas,excluded_schemas` | ok |
| delete (classification off) | `DELETE` | ok |

It also reports which columns that look like personal data (email, phone, address, postcode, birth date, card
digits) each version leaves unscanned:

| Version | Schemas scanned | Tables | PII-like columns scanned | PII-like columns missed |
|---|---|---|---|---|
| v1 | loyalty, pos | 4 | 4 | 6 (5 in `ecommerce`, 1 in `sandbox`) |
| v2 | + ecommerce | 6 | 9 | 1 (`sandbox.analyst_scratch.email`) |
| v3 | all but sandbox | 7 | 9 | 1 (`sandbox.analyst_scratch.email`) |

## Why it matters
Until this API went GA the scope was set by clicking in Catalog Explorer, so nobody could review it or see that the
ecommerce launch left five customer columns unscanned. As code, the scope is a reviewed file, re-applying it is a
no-op, and v3 flips the default: new schemas are scanned unless someone opts them out. The report also shows the
cost of opting out: `sandbox` holds an email column, which is a reason to clean that schema rather than skip it.

## How to run
```bash
python features/databricks/day-25-pii-scan-scope/scan_scope.py --local        # writes result.json
python features/databricks/day-25-pii-scan-scope/scan_scope.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN
pytest -q tests/test_day25_databricks_data_classification.py
```
The local run plays the scenario against an in-memory API that follows the documented rules. The test also plays it
**over HTTP** through the real REST client against a local server, so paths, `update_mask` values and cleanup are
checked without cloud access.

`cloud.sh` (GitHub Actions) needs **no compute**: it creates an empty scratch catalog `microfeatures_day25` with the
five schemas through the Unity Catalog REST API, plays the scenario, and fails if any step's outcome or the scope read
back differs from local. The config and the catalog are deleted on exit. Classification is never turned on for an
existing catalog: if the workspace refuses the scratch catalog, the run says so as a warning and the scenario counts
as verified locally only.

## Cost
A dozen REST calls. The scratch catalog is empty, so classification has nothing to scan; the config exists for a few
seconds. Fine on Free Edition.

## Result
5 write calls for 3 policy versions (create, rejected duplicate create, two masked PATCHes, delete), 1 no-op
re-apply. v1 missed 6 personal-data columns; v2 and v3 miss only the sandbox email. Full numbers in
[`result.json`](result.json).
