# Day 47 · Databricks · One basket query, three price books, switched with SET PATH

**New platform feature:** **`SET PATH` and `current_path()`** (Databricks SQL **2026.43** Preview, release note
**2026-10-19**, "Control name resolution with SET PATH"; Databricks Runtime 19 and above): an ordered list of schemas
that unqualified table, view and function names are resolved against, persisted into views and SQL functions at
create time. From the [Databricks SQL release notes 2026](https://docs.databricks.com/aws/en/sql/release-notes/2026#control-name-resolution-with-set-path)
([SET PATH](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-conf-mgmt-set-path),
[current_path](https://docs.databricks.com/aws/en/sql/language-manual/functions/current_path),
[name resolution](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-name-resolution)).

## What it does
A store keeps everyday prices in `cmf_day47_live.shelf_prices` and the weekend promotion as a full price book in
`cmf_day47_promo.shelf_prices`; the basket (6 lines, 14 units) is only in `live`. The report query never names a
schema:

```sql
SELECT sum(b.qty * p.price_cents) AS total FROM basket b JOIN shelf_prices p USING (sku)
```

| SET PATH | Expected |
|---|---|
| `SYSTEM_PATH, <cat>.cmf_day47_live` | 2,050 cents (everyday) |
| `SYSTEM_PATH, <cat>.cmf_day47_promo, <cat>.cmf_day47_live` | 1,745 cents: promo's `shelf_prices` shadows live's, `basket` falls through to live |
| `SYSTEM_PATH, <cat>.cmf_day47_not_there, <cat>.cmf_day47_live` | 2,050: a missing schema is skipped |
| view created under the promo path, then `SET PATH` back to live | view 1,745 (captured path), same query typed directly 2,050 |
| `SET PATH = cmf_day47_live` | refused: `INVALID_SQL_PATH_SCHEMA_REFERENCE` (one-part name) |
| `SET PATH = SYSTEM_PATH, system.builtin` | refused: `DUPLICATE_SQL_PATH_ENTRY`; path unchanged after both |

Every step also reads `current_path()` (e.g. `system.builtin,system.ai,workspace.cmf_day47_promo,workspace.cmf_day47_live`).
[`path_prices.py`](path_prices.py) holds a pure-Python model of the documented resolution rules (the reference), a
DuckDB `search_path` run of the two plain cases as an independent engine, and the serverless-job steps. `SET PATH`
must hold across statements, so the cloud run is one Spark session in a serverless job, not separate Statement
Execution API calls.

## Why it matters
Promotions, what-if price books and dev/prod schemas usually mean copies of the same SQL with different schema names.
With a path the query stays the same and the session decides which schema wins; views keep the path they were built
with, so switching the session can't silently re-point a published view.

## How to run
```bash
python features/databricks/day-47-price-book-path/path_prices.py     # model + DuckDB, writes result.json
pytest -q tests/test_day47_databricks_set_path.py                     # no credentials needed
bash features/databricks/day-47-price-book-path/cloud.sh             # GitHub Actions: serverless job
```

## Cost
One short serverless job (Free Edition: no charge); three tiny Delta tables and one view in two scratch schemas,
deleted with the workspace folder and volume on exit.

## Result
Local: everyday 2,050 cents, promo 1,745 (saving 305) on 6 lines / 14 units; DuckDB's `search_path` agrees on both
plain cases. Seven scenarios checked. Numbers in [`result.json`](result.json).

**Cloud:** not run yet. The Free Edition workspace has refused compute since 2026-10-24 (`FEATURE_DISABLED` /
organization not active); `cloud.sh` reports that as a warning and leaves nothing behind. Status `built`.
