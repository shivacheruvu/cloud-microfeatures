"""Day 43 (Databricks): reload a documented cost table every week with CREATE OR REPLACE TABLE ... AS SELECT and keep
its table and column comments. Serverless release of 2026-07-06 (Databricks Runtime 18 updates): "CREATE OR REPLACE
TABLE now preserves existing column and table comments by default. Previously, comments were dropped when recreating
a table."

  python table_replace.py                        # local: pure-Python reference, writes result.json
  python table_replace.py WORKDIR CATALOG SCHEMA # serverless job: runs the steps in Unity Catalog, result to WORKDIR

The story: `supplier_case_costs` holds the Harbor Farms weekly sheet (the six SKUs Google Cloud day 42 parses out of
a PDF). It is documented for Catalog Explorer and Genie: a table comment and a comment on every column ("USD per
case, FOB warehouse" ...). Each week the loader rebuilds it from the new sheet with one CTAS that also adds the unit
cost and the week; the CTAS says nothing about comments. Before this release every reload wiped the documentation.

Steps (serverless job, Spark SQL against Unity Catalog, Delta):
  1. CREATE TABLE supplier_case_costs (... COMMENT ...) COMMENT '...' + week 46 rows
  2. snapshot: information_schema.tables (comment), information_schema.columns (name, type, comment), rows
  3. CREATE OR REPLACE TABLE supplier_case_costs AS SELECT ..., round(case_cost / units, 2) AS unit_cost, 47 AS week
     FROM VALUES (week 47 sheet)          -- no COMMENT anywhere
  4. snapshot again + the operation of the latest DESCRIBE HISTORY entry
Must hold: the table comment and the four column comments are unchanged after the reload, the table has the 6 new
columns and its rows equal the pure-Python reference. Reported: comments on the two new columns (none expected) and
the Delta history operation.
"""
from __future__ import annotations

import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

TABLE = "supplier_case_costs"
TABLE_COMMENT = "Harbor Farms weekly case costs, one row per SKU, reloaded every Monday (owner: pricing)"
COLUMN_COMMENTS = {
    "sku": "Harbor Farms supplier SKU (HF-nnnn)",
    "product": "Product name as printed on the supplier sheet",
    "units": "Units per case",
    "case_cost": "USD per case, FOB warehouse",
}
WEEK46 = [  # sku, product, units per case, case cost: the same sheet as Google Cloud day 42
    ("HF-1001", "Whole milk", 4, "13.96"),
    ("HF-1002", "Reduced fat milk 2%", 4, "13.56"),
    ("HF-2001", "Greek yogurt plain", 6, "23.40"),
    ("HF-2002", "Salted butter", 12, "41.88"),
    ("HF-3001", "Large eggs grade A", 15, "37.35"),
    ("HF-3002", "Sharp cheddar block", 12, "30.60"),
]
WEEK47 = {"HF-1001": "14.36", "HF-2002": "43.08", "HF-3001": "35.85"}  # new case costs; the rest unchanged
COLUMNS_BEFORE = ["sku", "product", "units", "case_cost"]
COLUMNS_AFTER = COLUMNS_BEFORE + ["unit_cost", "week"]


# ---------- pure-Python reference ----------
def unit_cost(cost: str, units: int) -> str:
    return str((Decimal(cost) / units).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def week47_rows() -> list[tuple]:
    return [(s, p, u, WEEK47.get(s, c)) for s, p, u, c in WEEK46]


def rows_after() -> list[list]:
    return [[s, p, u, c, unit_cost(c, u), 47] for s, p, u, c in week47_rows()]


def reference() -> dict:
    after = rows_after()
    cheapest = min(after, key=lambda r: Decimal(r[4]))
    meta = {"table_comment": TABLE_COMMENT, "column_comments": dict(COLUMN_COMMENTS)}
    return {"rows": len(after), "columns_before": COLUMNS_BEFORE, "columns_after": COLUMNS_AFTER,
            "rows_after": after, "changed_skus": sorted(WEEK47),
            "case_cost_total": str(sum(Decimal(r[3]) for r in after)),
            "cheapest_unit": {"sku": cheapest[0], "unit_cost": cheapest[4]},
            "before": meta, "after": meta, "new_column_comments": {"unit_cost": None, "week": None},
            "kept": {"table_comment": True, "column_comments": 4}}


# ---------- Spark ----------
def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _values(rows) -> str:
    return ", ".join(f"({_q(s)}, {_q(p)}, {u}, CAST({c} AS DECIMAL(8,2)))" for s, p, u, c in rows)


def create_sql(fq: str, using: str = "") -> list[str]:
    cols = ", ".join(f"{c} {t} COMMENT {_q(COLUMN_COMMENTS[c])}"
                     for c, t in zip(COLUMNS_BEFORE, ("STRING", "STRING", "INT", "DECIMAL(8,2)")))
    return [f"CREATE TABLE {fq} ({cols}){using} COMMENT {_q(TABLE_COMMENT)}",
            f"INSERT INTO {fq} VALUES {_values(WEEK46)}"]


def reload_sql(fq: str, verb: str = "CREATE OR REPLACE TABLE", using: str = "") -> str:
    # no COMMENT anywhere: since the 2026-07-06 serverless release Databricks keeps the existing ones
    return (f"{verb} {fq}{using} AS SELECT sku, product, units, case_cost, "
            f"round(case_cost / units, 2) AS unit_cost, 47 AS week "
            f"FROM VALUES {_values(week47_rows())} AS v(sku, product, units, case_cost)")


def _rows(spark, fq: str) -> list[list]:
    return [[str(v) if not isinstance(v, (int, str)) else v for v in r]
            for r in spark.sql(f"SELECT * FROM {fq} ORDER BY sku").collect()]


def snapshot_uc(spark, catalog: str, schema: str) -> dict:
    t = spark.sql(f"SELECT comment FROM {catalog}.information_schema.tables "
                  f"WHERE table_schema = {_q(schema)} AND table_name = {_q(TABLE)}").collect()
    cols = spark.sql(f"SELECT column_name, comment FROM {catalog}.information_schema.columns "
                     f"WHERE table_schema = {_q(schema)} AND table_name = {_q(TABLE)} "
                     f"ORDER BY ordinal_position").collect()
    fq = f"{catalog}.{schema}.{TABLE}"
    return {"table_comment": t[0].comment if t else None,
            "comments": {r.column_name: r.comment for r in cols},
            "columns": spark.table(fq).columns, "rows": _rows(spark, fq)}


def run_uc(spark, catalog: str, schema: str) -> dict:
    fq = f"{catalog}.{schema}.{TABLE}"
    spark.sql(f"DROP TABLE IF EXISTS {fq}")  # a leftover from an interrupted run
    for s in create_sql(fq):
        spark.sql(s)
    before = snapshot_uc(spark, catalog, schema)
    spark.sql(reload_sql(fq))
    after = snapshot_uc(spark, catalog, schema)
    hist = spark.sql(f"DESCRIBE HISTORY {fq} LIMIT 1").collect()
    return summarise(before, after, operation=hist[0].operation if hist else None)


def summarise(before: dict, after: dict, operation: str | None) -> dict:
    rows = after["rows"]
    cheapest = min(rows, key=lambda r: Decimal(str(r[4]))) if rows and len(rows[0]) > 4 else [None] * 5
    old = {r[0]: str(r[3]) for r in before["rows"]}
    meta = lambda s: {"table_comment": s["table_comment"],  # noqa: E731
                      "column_comments": {c: s["comments"].get(c) for c in COLUMNS_BEFORE}}
    return {"rows": len(rows), "columns_before": before["columns"], "columns_after": after["columns"],
            "rows_after": rows, "changed_skus": sorted(r[0] for r in rows if old.get(r[0]) != str(r[3])),
            "case_cost_total": str(sum(Decimal(str(r[3])) for r in rows)),
            "cheapest_unit": {"sku": cheapest[0], "unit_cost": cheapest[4]},
            "before": meta(before), "after": meta(after),
            "new_column_comments": {c: after["comments"].get(c) for c in COLUMNS_AFTER if c not in COLUMNS_BEFORE},
            "kept": {"table_comment": after["table_comment"] == before["table_comment"] == TABLE_COMMENT,
                     "column_comments": sum(after["comments"].get(c) == before["comments"].get(c) == v
                                            for c, v in COLUMN_COMMENTS.items())},
            "operation": operation}


def run_oss_spark(spark) -> dict:
    """Local contrast in open-source Spark 4.3 without Delta: a parquet table can't be REPLACEd, so the weekly reload
    is a drop and a CTAS, which is what a replace amounted to for comments before this release: they are gone."""
    spark.sql(f"DROP TABLE IF EXISTS {TABLE}")
    for s in create_sql(TABLE, using=" USING parquet"):
        spark.sql(s)

    def snap() -> dict:
        d = spark.sql(f"DESCRIBE TABLE EXTENDED {TABLE}").collect()
        comments, table_comment, in_cols = {}, None, True
        for r in d:
            if not r.col_name or r.col_name.startswith("#"):
                in_cols = False
            elif in_cols:
                comments[r.col_name] = r.comment
            elif r.col_name == "Comment":
                table_comment = r.data_type
        return {"table_comment": table_comment, "comments": comments, "columns": spark.table(TABLE).columns,
                "rows": _rows(spark, TABLE)}
    before = snap()
    spark.sql(f"DROP TABLE {TABLE}")
    spark.sql(reload_sql(TABLE, verb="CREATE TABLE", using=" USING parquet"))
    return summarise(before, snap(), operation=None)


def main(argv: list[str]) -> int:
    if argv:  # serverless job: WORKDIR CATALOG SCHEMA
        import pyspark
        from pyspark.sql import SparkSession
        out = run_uc(SparkSession.builder.getOrCreate(), argv[1], argv[2])
        out["engine"] = (f"CREATE OR REPLACE TABLE AS SELECT in Unity Catalog on a Databricks serverless job "
                         f"(pyspark {pyspark.__version__})")
        text = json.dumps(out, sort_keys=True)
        print("DAY43_RESULT " + text)  # read back from the task's logs if the output file can't be read
        Path(argv[0].rstrip("/")).mkdir(parents=True, exist_ok=True)
        Path(argv[0].rstrip("/"), "result.json").write_text(text)
        return 0
    out = {"engine": "pure-Python reference (local) / CREATE OR REPLACE TABLE AS SELECT in Unity Catalog on a "
                     "Databricks serverless job (cloud)", **reference()}
    (Path(__file__).resolve().parent / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("rows", "columns_after", "changed_skus", "cheapest_unit", "kept")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
