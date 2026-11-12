# Day 38 · Databricks · Night restock pick list: a scalar UDF straight into `range()`

**New platform feature:** **scalar UDFs as table-valued function arguments**: "You can now pass scalar user-defined
functions (UDFs) as arguments to table-valued functions." Databricks SQL **2026.32** (Preview 2026-08-11, current
channel 2026-08-24), in the [Databricks SQL release notes 2026](https://docs.databricks.com/aws/en/sql/release-notes/2026)
([TVF invocation](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-tvf)).
Serverless, so it runs on Databricks Free Edition.

## What it does
The night count for one store ([`restock_cases.py`](restock_cases.py), 8 shelf lines: units on the shelf, par, case
pack) becomes the night crew's pick list, one row per case to bring from the back room. A temporary SQL UDF does the
arithmetic:

```sql
CREATE OR REPLACE TEMPORARY FUNCTION cases_to_pick(on_hand INT, par INT, pack INT)
RETURNS INT
RETURN CAST(GREATEST(0, CEIL((par - on_hand) / pack)) AS INT);

SELECT 'MLK-2L' AS sku, 'A01' AS aisle, id AS case_no
FROM range(1, cases_to_pick(7, 30, 6) + 1)        -- the UDF is an argument of the TVF
UNION ALL ...
```

Open-source Spark 4.3 refuses that query with `NON_FOLDABLE_ARGUMENT` ("The function `range` requires the parameter
`end` to be a foldable expression"), and so did Databricks before 2026.32. The old workaround, which still runs
everywhere, puts the UDF in the SELECT list and makes rows with `LATERAL VIEW explode(sequence(1, n))`.

The serverless job ([`run_cloud.py`](run_cloud.py) → `restock_cases.py`) runs **both** and requires each to equal the
plain-Python reference row for row; a refusal of `range(cases_to_pick(...))` is an error.

## Why it matters
Business rules like "cases to pick" live in one governed UDF. Being able to pass it straight into a TVF means the
rule drives row generation directly (pick labels, calendar slots, sample sizes) instead of a detour through arrays and
`explode`, and the same rule isn't re-typed as a literal.

## How to run
```bash
python features/databricks/day-38-restock-cases-udf-tvf/restock_cases.py   # reference, writes result.json
pytest -q tests/test_day38_databricks_udf_tvf_args.py                        # incl. the old way on local pyspark
bash features/databricks/day-38-restock-cases-udf-tvf/cloud.sh             # GitHub Actions, needs Databricks secrets
```

## Cost
One serverless job run of a few seconds on Free Edition (no SQL warehouse); the scratch schema, volume and workspace
folder are deleted on exit.

## Result (local reference; open-source Spark's `explode(sequence)` gives the same)
| Aisle | SKU | On shelf / par / pack | Cases |
|---|---|---|---|
| A01 | MLK-2L | 7 / 30 / 6 | 4 |
| A01 | YOG-500 | 11 / 24 / 12 | 2 |
| A02 | EGG-12 | 18 / 18 / 6 | 0 (at par) |
| B07 | PAS-500 | 3 / 40 / 20 | 2 |
| B07 | RIC-2KG | 9 / 12 / 8 | 1 |
| B08 | OIL-1L | 25 / 20 / 6 | 0 (overstock, not negative) |
| B09 | CRL-500 | 0 / 36 / 12 | 3 |
| F03 | PEA-1KG | 5 / 30 / 10 | 3 |

**15 cases, 162 units**; by aisle A01 6, B07 3, B09 3, F03 3. Numbers in [`result.json`](result.json).

**Cloud:** expected to warn and verify locally until the Free Edition workspace is active again (it has refused
compute since 2026-10-24).
