# Day 39 · Databricks · Get a dropped price-file volume back with `UNDROP VOLUME`

**New platform feature:** **recover dropped Unity Catalog volumes with `UNDROP VOLUME`** (2026-11-07): "Restores a
volume dropped within 7 days, with its privileges and tags", on Databricks SQL and Databricks Runtime 19+. From the
[Databricks platform release notes, November 2026](https://docs.databricks.com/aws/en/release-notes/product/)
([UNDROP VOLUME](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-undrop-volume),
[SHOW VOLUMES DROPPED](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-volumes-dropped)).
Serverless, so it runs on Databricks Free Edition.

## What it does
Three stores' shelf price files ([`data/`](data/), 15 rows) sit in a managed volume `price_files` with a comment and a
`READ VOLUME` grant to `account users`. Someone drops it. The recovery:

```sql
DROP VOLUME workspace.cmf_day39.price_files;              -- the accident
SHOW VOLUMES DROPPED IN workspace.cmf_day39;              -- volume_name, volume_id, owner, created_at, ...
UNDROP VOLUME WITH ID '6ca7be55-...';                     -- there is no by-name form
```

[`undrop_volume.py`](undrop_volume.py) is the serverless job: it snapshots the volume (every file's SHA-256, the rows
and price total Spark reads from it, the comment, the grant), drops it, checks the path is gone, picks its row from
`SHOW VOLUMES DROPPED` **by the ID recorded at creation, not by name** (an earlier drop of a volume with the same name
is listed too), runs `UNDROP VOLUME WITH ID`, and snapshots again. [`run_cloud.py`](run_cloud.py) sets the volume up
over REST, submits the job and requires both snapshots to equal the local reference, and the restored volume to keep
its ID and comment.

## Why it matters
A dropped managed volume used to mean the files were gone: re-upload from wherever they came from, re-grant, re-tag.
`UNDROP VOLUME` makes `DROP VOLUME` recoverable for 7 days, files and permissions included, the same safety net
`UNDROP TABLE` gives tables.

## How to run
```bash
python features/databricks/day-39-price-volume-undrop/undrop_volume.py   # reference, writes result.json
pytest -q tests/test_day39_databricks_undrop_volume.py                    # incl. local pyspark read of the files
bash features/databricks/day-39-price-volume-undrop/cloud.sh             # GitHub Actions, needs Databricks secrets
```

## Cost
REST calls plus one serverless job run of under a minute on Free Edition (no SQL warehouse). The workspace folder,
volumes and schema are deleted on exit; the dropped copy is purged by Databricks after 7 days.

## Result (local reference)
| File | Rows | Prices total |
|---|---|---|
| prices_chi01.csv | 5 | 21.55 |
| prices_chi02.csv | 4 | 17.56 |
| prices_chi03.csv | 6 | 20.24 |

**3 files, 15 rows, 59.35**: the undropped volume must give back exactly these bytes (SHA-256 per file in
[`result.json`](result.json)), the comment, the `READ VOLUME` grant to `account users` and the same volume ID.
