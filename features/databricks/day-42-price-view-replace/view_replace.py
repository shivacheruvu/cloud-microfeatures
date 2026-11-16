"""Day 42 (Databricks): change a governed view's definition with CREATE OR REPLACE VIEW and keep everything around it.
Databricks SQL 2026.35 (2026-08-24): on Unity Catalog views, CREATE OR REPLACE VIEW is an atomic replacement that
preserves the view's identity, grants, tags and comment by default.

  python view_replace.py                        # local: pure-Python reference, writes result.json
  python view_replace.py WORKDIR CATALOG SCHEMA # serverless job: runs the steps in Unity Catalog, result to WORKDIR

The story: the store dashboards read `shelf_cost_v` (SKU, product, case cost). It carries a comment, two governed
tags and a SELECT grant to `account users`. Pricing now wants a unit cost column, so the view is replaced with a
wider definition. Before 2026.35 (and in open-source Spark 4.3, run locally in the test) a replace drops the comment
and properties and gives the view a new creation time, so someone has to re-apply them or dashboards lose access.

Steps (serverless job, Spark SQL against Unity Catalog):
  1. CREATE TABLE shelf_costs + 6 rows; CREATE VIEW shelf_cost_v COMMENT '...' AS SELECT sku, product, case_cost
  2. ALTER VIEW ... SET TAGS ('domain' = 'pricing', 'audience' = 'store-dashboards'); GRANT SELECT ... TO `account users`
  3. snapshot: information_schema.tables (comment, created), table_tags, table_privileges, columns, rows
  4. CREATE OR REPLACE VIEW shelf_cost_v AS SELECT sku, product, case_cost, units, round(case_cost / units, 2) ...
     (no COMMENT clause, no tags, no grant)
  5. snapshot again
Must hold: the comment, both tags and the grant are still there after the replace, the view has the 5 new columns
and its rows equal the pure-Python reference. Reported: whether the creation time (identity) stayed the same.
"""
from __future__ import annotations

import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

VIEW, TABLE = "shelf_cost_v", "shelf_costs"
COMMENT = "Case cost per SKU for the store dashboards (owner: pricing)"
TAGS = {"domain": "pricing", "audience": "store-dashboards"}
GRANTEE, PRIVILEGE = "account users", "SELECT"
ROWS = [  # sku, product, units per case, case cost: the same Harbor Farms sheet as Google Cloud day 42
    ("HF-1001", "Whole milk", 4, "13.96"),
    ("HF-1002", "Reduced fat milk 2%", 4, "13.56"),
    ("HF-2001", "Greek yogurt plain", 6, "23.40"),
    ("HF-2002", "Salted butter", 12, "41.88"),
    ("HF-3001", "Large eggs grade A", 15, "37.35"),
    ("HF-3002", "Sharp cheddar block", 12, "30.60"),
]
COLUMNS_BEFORE = ["sku", "product", "case_cost"]
COLUMNS_AFTER = ["sku", "product", "case_cost", "units", "unit_cost"]


# ---------- pure-Python reference ----------
def unit_cost(cost: str, units: int) -> str:
    return str((Decimal(cost) / units).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def rows_after() -> list[list]:
    return [[s, p, c, u, unit_cost(c, u)] for s, p, u, c in ROWS]


def reference() -> dict:
    after = rows_after()
    cheapest = min(after, key=lambda r: Decimal(r[4]))
    meta = {"comment": COMMENT, "tags": dict(sorted(TAGS.items())), "grants": [f"{PRIVILEGE} to {GRANTEE}"]}
    return {"rows": len(ROWS), "columns_before": COLUMNS_BEFORE, "columns_after": COLUMNS_AFTER,
            "rows_after": after, "cheapest_unit": {"sku": cheapest[0], "unit_cost": cheapest[4]},
            "before": meta, "after": meta,
            "kept": {"comment": True, "tags": True, "grant": True}}


# ---------- Spark ----------
def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def setup_sql(fq_table: str, fq_view: str, using: str = "") -> list[str]:
    values = ", ".join(f"({_q(s)}, {_q(p)}, {u}, CAST({c} AS DECIMAL(8,2)))" for s, p, u, c in ROWS)
    return [f"CREATE OR REPLACE TABLE {fq_table} (sku STRING, product STRING, units INT, case_cost DECIMAL(8,2)){using}",
            f"INSERT INTO {fq_table} VALUES {values}",
            f"CREATE VIEW {fq_view} COMMENT {_q(COMMENT)} AS SELECT sku, product, case_cost FROM {fq_table}"]


def governance_sql(fq_view: str) -> list[str]:
    tags = ", ".join(f"{_q(k)} = {_q(v)}" for k, v in TAGS.items())
    return [f"ALTER VIEW {fq_view} SET TAGS ({tags})", f"GRANT {PRIVILEGE} ON VIEW {fq_view} TO `{GRANTEE}`"]


def replace_sql(fq_table: str, fq_view: str) -> str:
    # no COMMENT, no TAGS, no GRANT: Databricks SQL 2026.35 keeps them; a plain replace elsewhere drops them
    return (f"CREATE OR REPLACE VIEW {fq_view} AS SELECT sku, product, case_cost, units, "
            f"round(case_cost / units, 2) AS unit_cost FROM {fq_table}")


def _rows(spark, fq_view: str) -> list[list]:
    return [[str(v) if not isinstance(v, (int, str)) else v for v in r]
            for r in spark.sql(f"SELECT * FROM {fq_view} ORDER BY sku").collect()]


def snapshot_uc(spark, catalog: str, schema: str) -> dict:
    t = spark.sql(f"SELECT comment, CAST(created AS STRING) AS created FROM {catalog}.information_schema.tables "
                  f"WHERE table_schema = {_q(schema)} AND table_name = {_q(VIEW)}").collect()
    tags = spark.sql(f"SELECT tag_name, tag_value FROM {catalog}.information_schema.table_tags "
                     f"WHERE schema_name = {_q(schema)} AND table_name = {_q(VIEW)}").collect()
    grants = spark.sql(f"SELECT privilege_type, grantee FROM {catalog}.information_schema.table_privileges "
                       f"WHERE table_schema = {_q(schema)} AND table_name = {_q(VIEW)} "
                       f"AND grantee = {_q(GRANTEE)}").collect()
    fq = f"{catalog}.{schema}.{VIEW}"
    return {"comment": t[0].comment if t else None, "created": t[0].created if t else None,
            "tags": dict(sorted((r.tag_name, r.tag_value) for r in tags)),
            "grants": sorted(f"{r.privilege_type} to {r.grantee}" for r in grants),
            "columns": spark.table(fq).columns, "rows": _rows(spark, fq)}


def run_uc(spark, catalog: str, schema: str) -> dict:
    fq_table, fq_view = f"{catalog}.{schema}.{TABLE}", f"{catalog}.{schema}.{VIEW}"
    spark.sql(f"DROP VIEW IF EXISTS {fq_view}")  # a leftover from an interrupted run
    for s in setup_sql(fq_table, fq_view) + governance_sql(fq_view):
        spark.sql(s)
    before = snapshot_uc(spark, catalog, schema)
    spark.sql(replace_sql(fq_table, fq_view))
    after = snapshot_uc(spark, catalog, schema)
    return summarise(before, after, identity_kept=bool(before["created"]) and before["created"] == after["created"])


def summarise(before: dict, after: dict, identity_kept: bool | None) -> dict:
    cheapest = min(after["rows"], key=lambda r: Decimal(str(r[4]))) if after["rows"] else [None] * 5
    meta = lambda s: {"comment": s["comment"], "tags": s["tags"], "grants": s["grants"]}  # noqa: E731
    return {"rows": len(after["rows"]), "columns_before": before["columns"], "columns_after": after["columns"],
            "rows_after": after["rows"], "cheapest_unit": {"sku": cheapest[0], "unit_cost": cheapest[4]},
            "before": meta(before), "after": meta(after),
            "kept": {"comment": after["comment"] == before["comment"] == COMMENT,
                     "tags": after["tags"] == before["tags"] == TAGS,
                     "grant": f"{PRIVILEGE} to {GRANTEE}" in after["grants"]},
            "identity_kept": identity_kept}


def run_oss_spark(spark) -> dict:
    """The same replace in open-source Spark (local test): comment and properties set before the replace are dropped
    and the view is re-created. Tags and grants don't exist there, so TBLPROPERTIES stand in for the tags."""
    spark.sql(f"DROP VIEW IF EXISTS {VIEW}")
    spark.sql(f"DROP TABLE IF EXISTS {TABLE}")
    for s in setup_sql(TABLE, VIEW, using=" USING parquet"):
        spark.sql(s.replace("CREATE OR REPLACE TABLE", "CREATE TABLE"))  # no REPLACE TABLE without Delta
    props = ", ".join(f"{_q(k)} = {_q(v)}" for k, v in TAGS.items())
    spark.sql(f"ALTER VIEW {VIEW} SET TBLPROPERTIES ({props})")

    def snap() -> dict:
        d = {r.col_name: r.data_type for r in spark.sql(f"DESCRIBE TABLE EXTENDED {VIEW}").collect()}
        tbl = {k: v for k, v in (p.split("=", 1) for p in (d.get("Table Properties") or "[]")[1:-1].split(", ")
                                 if "=" in p) if k in TAGS}
        return {"comment": d.get("Comment"), "created": d.get("Created Time"), "tags": dict(sorted(tbl.items())),
                "grants": [], "columns": spark.table(VIEW).columns, "rows": _rows(spark, VIEW)}
    before = snap()
    spark.sql(replace_sql(TABLE, VIEW))
    return summarise(before, snap(), identity_kept=None)


def main(argv: list[str]) -> int:
    if argv:  # serverless job: WORKDIR CATALOG SCHEMA
        import pyspark
        from pyspark.sql import SparkSession
        out = run_uc(SparkSession.builder.getOrCreate(), argv[1], argv[2])
        out["engine"] = f"CREATE OR REPLACE VIEW in Unity Catalog on a Databricks serverless job (pyspark {pyspark.__version__})"
        text = json.dumps(out, sort_keys=True)
        print("DAY42_RESULT " + text)  # read back from the task's logs if the output file can't be read
        Path(argv[0].rstrip("/")).mkdir(parents=True, exist_ok=True)
        Path(argv[0].rstrip("/"), "result.json").write_text(text)
        return 0
    out = {"engine": "pure-Python reference (local) / CREATE OR REPLACE VIEW in Unity Catalog on a Databricks "
                     "serverless job (cloud)", **reference()}
    (Path(__file__).resolve().parent / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("rows", "columns_after", "cheapest_unit", "kept")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
