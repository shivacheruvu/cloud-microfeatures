# Day 42 · Databricks · Widen a governed price view without losing its comment, tags or grants

**New platform feature:** **`CREATE OR REPLACE VIEW` as an atomic replacement on Unity Catalog views** (Databricks
SQL **2026.35**, preview **2026-08-24**): "performs an atomic replacement that preserves identity, grants, tags, and
comments by default." From the [Databricks SQL release notes 2026](https://docs.databricks.com/aws/en/sql/release-notes/2026).

## What it does
The store dashboards read `shelf_cost_v` (SKU, product, case cost) over the Harbor Farms price sheet (the same six
SKUs Google Cloud day 42 parses out of a PDF). The view carries a comment, two governed tags (`domain = pricing`,
`audience = store-dashboards`) and `GRANT SELECT ... TO account users`. Pricing wants a unit cost column, so the view
is replaced with a wider definition, and **the replace statement says nothing about the comment, tags or grant**:

```sql
CREATE OR REPLACE VIEW shelf_cost_v AS
SELECT sku, product, case_cost, units, round(case_cost / units, 2) AS unit_cost FROM shelf_costs
```

[`view_replace.py`](view_replace.py) runs as a one-off serverless job (environment version 6): it builds the table and
the governed view in a scratch schema, snapshots `information_schema.tables` (comment, created), `table_tags`,
`table_privileges` and the rows, runs the replace, and snapshots again.

**Must hold** (else the cloud run fails): after the replace the comment, both tags and the `SELECT` grant are
unchanged, the view has the 5 new columns, and its 6 rows equal the pure-Python reference. **Reported:** whether the
creation time (identity) stayed the same; a reset is a warning.

**Local contrast (in the test):** open-source Spark 4.3 runs the same statements and returns the same rows, but its
replace **drops the comment and the properties** (standing in for tags) and re-creates the view, which is the
behaviour this release removes for Unity Catalog views.

## Why it matters
Views are the contract dashboards and other teams read. Before, a definition change meant re-applying the comment,
tags and grants in the same deploy, or the dashboard lost access until someone noticed. Now the replace is one
atomic statement and governance stays attached.

## How to run
```bash
python features/databricks/day-42-price-view-replace/view_replace.py      # reference, writes result.json
pytest -q tests/test_day42_databricks_view_replace.py                     # no credentials (local pyspark + fakes)
bash features/databricks/day-42-price-view-replace/cloud.sh               # GitHub Actions: DATABRICKS_HOST / TOKEN
```

## Cost
One short serverless job (a few DDL statements and information_schema reads on 6 rows): within Free Edition's
serverless quota. Scratch schema, volume and workspace folder are deleted on exit.

## Result
Reference (local): 6 rows, 3 → 5 columns, cheapest per unit **HF-3001** (large eggs, $2.49 per dozen); comment, 2 tags
and 1 grant kept. Open-source Spark 4.3: same rows, comment and properties gone after the replace. The cloud run is
refused while the Free Edition account is inactive (see the report), so this is verified locally only for now.
Numbers in [`result.json`](result.json).
