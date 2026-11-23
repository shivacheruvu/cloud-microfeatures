# Day 49 · Google Cloud · Row-level security audit of a store price table from INFORMATION_SCHEMA

**New platform feature:** **`INFORMATION_SCHEMA.ROW_ACCESS_POLICIES` view** (GA per the BigQuery release notes,
**2026-11-18**): query metadata about the active row-level access policies in a dataset or region, including each
policy's filter predicate. From the [BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)
(November 18, 2026) ([reference](https://docs.cloud.google.com/bigquery/docs/information-schema-row-access-policies)).

## What it does
A chain keeps all four stores' shelf prices in one table and controls who sees which rows with row access policies.
The run (in a short-lived dataset) creates the table and three policies, all granted to the runner's own service
account:

| Policy | Filter | Rows it opens |
|---|---|---|
| `north_stores` | `region = 'north'` | 6 |
| `south_promos` | `region = 'south' AND on_promo` | 2 |
| `closed_store_s9` | `store_id = 'S9'` | 0 (dead: the store closed) |

Then it reads the policies back with one query:

```sql
SELECT * FROM `<dataset>`.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES WHERE table_name = 'store_prices'
```

and [`audit.py`](audit.py) **replays every stored filter predicate in DuckDB** over a local copy of the rows, so the
audit (rows per policy, dead policies, rows no policy covers) comes from the metadata alone. Finally the runner
queries the protected table, which must return exactly the union the replay predicts.

**Must hold** (else the cloud run fails): the view lists exactly the 3 policies; each stored predicate replays to the
same rows as its definition (6, 2, 0); the runner sees exactly those 8 rows and none of the 4 south everyday rows that
no policy covers. The run also reports how many predicates came back verbatim and which columns the view has.

## Why it matters
Row-level security fails quietly: a policy with a typo opens nothing, a missing policy hides rows from everyone, and
until now the only way to list policies was table by table (`bq ls --row_access_policies`). With the view, a
scheduled SQL audit can cover a whole dataset or region and flag dead policies and uncovered rows.

## How to run
```bash
python features/gcp/day-49-price-policy-audit/audit.py                 # DuckDB replay, writes result.json
python features/gcp/day-49-price-policy-audit/audit.py --sql view DS    # the INFORMATION_SCHEMA query
pytest -q tests/test_day49_gcp_row_access_policies.py                   # no credentials needed
bash features/gcp/day-49-price-policy-audit/cloud.sh                    # GitHub Actions (keyless)
```

## Cost
Five small queries on a 12-row table plus one `INFORMATION_SCHEMA` query (10 MiB minimum each): well under $0.01.
The dataset is deleted at the end of the run (tables also expire after an hour).

## Result
Local replay: 12 rows, 3 policies opening 6 / 2 / 0 rows; 8 rows visible to the runner, 4 uncovered
(S3 bread and eggs, S4 bread and milk), 1 dead policy (`closed_store_s9`). Figures in [`result.json`](result.json).
The cloud run must match.
