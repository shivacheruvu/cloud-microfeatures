"""Day 44 (Databricks): append store scan batches whose column types are narrower than the table's, with
DataFrame `writeTo().append()` and `write.mode("append").saveAsTable()`. Serverless release of 2026-07-06 (Databricks
Runtime 18 updates): these writes "cast type-compatible columns to the target schema instead of failing with
DELTA_FAILED_TO_MERGE_FIELDS".

  python append_cast.py                          # local: pure-Python reference, writes result.json
  python append_cast.py WORKDIR CATALOG SCHEMA   # serverless job: runs the steps in Unity Catalog, result to WORKDIR

The story: `store_scans` is the chain-wide scan table (store_id INT, qty BIGINT, unit_price DECIMAL(10,2)). Store 7's
old POS exports store_id as SMALLINT, qty as INT and prices as DECIMAL(6,2); store 9's exports qty as INT and prices
as DECIMAL(5,2). Before this release each batch needed hand-written casts (or failed the job); now the writer casts.

Steps (serverless job, Unity Catalog, Delta):
  1. CREATE TABLE store_scans (...) + 3 seed rows from store 3
  2. store 7 batch (4 rows, narrow types) -> df.writeTo(table).append()
  3. store 9 batch (3 rows, narrow types) -> df.write.mode("append").saveAsTable(table)
  4. read back schema, rows and DESCRIBE HISTORY operations
Must hold: both appends succeed, the table schema is unchanged (no column widened or narrowed, no column added), and
the rows equal the pure-Python reference (10 scans; per-store units and revenue).
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

TABLE = "store_scans"
SCHEMA_DDL = [("store_id", "INT"), ("sku", "STRING"), ("qty", "BIGINT"), ("unit_price", "DECIMAL(10,2)"),
              ("scan_date", "DATE")]
SEED = [(3, "HF-1001", 2, "3.59", "2026-11-16"), (3, "PN-6001", 1, "4.29", "2026-11-16"),
        (3, "PR-4001", 6, "0.29", "2026-11-16")]
STORE7 = {  # old POS: narrower types than the table
    "ddl": "store_id SMALLINT, sku STRING, qty INT, unit_price DECIMAL(6,2), scan_date DATE",
    "rows": [(7, "HF-3001", 1, "4.99", "2026-11-17"), (7, "HF-2002", 2, "5.49", "2026-11-17"),
             (7, "PN-6003", 3, "3.79", "2026-11-17"), (7, "FZ-8001", 1, "6.99", "2026-11-17")],
    "method": "writeTo().append()",
}
STORE9 = {
    "ddl": "store_id INT, sku STRING, qty INT, unit_price DECIMAL(5,2), scan_date DATE",
    "rows": [(9, "DA-1005", 2, "4.49", "2026-11-17"), (9, "BK-5001", 1, "5.99", "2026-11-17"),
             (9, "HH-7001", 4, "2.89", "2026-11-17")],
    "method": 'write.mode("append").saveAsTable()',
}
SPARK_TYPES = {"store_id": "int", "sku": "string", "qty": "bigint", "unit_price": "decimal(10,2)", "scan_date": "date"}


# ---------- pure-Python reference ----------
def _norm(r) -> list:
    return [int(r[0]), r[1], int(r[2]), str(Decimal(str(r[3])).quantize(Decimal("0.01"))), str(r[4])]


def by_store(rows: list[list]) -> dict:
    out: dict[str, dict] = {}
    for s, _, q, p, _ in rows:
        d = out.setdefault(str(s), {"scans": 0, "units": 0, "revenue": Decimal("0")})
        d["scans"] += 1
        d["units"] += q
        d["revenue"] += q * Decimal(p)
    return {k: {**v, "revenue": str(v["revenue"])} for k, v in sorted(out.items())}


def reference() -> dict:
    rows = sorted(_norm(r) for r in SEED + STORE7["rows"] + STORE9["rows"])
    return {"rows": len(rows), "schema_before": SPARK_TYPES, "schema_after": SPARK_TYPES,
            "batch_types": {"store_7": STORE7["ddl"], "store_9": STORE9["ddl"]},
            "appends": {"store_7": {"method": STORE7["method"], "ok": True},
                        "store_9": {"method": STORE9["method"], "ok": True}},
            "rows_after": rows, "by_store": by_store(rows),
            "revenue_total": str(sum(q * Decimal(p) for _, _, q, p, _ in rows))}


# ---------- Spark ----------
def _batch(spark, b: dict):
    import datetime
    data = [(s, k, q, Decimal(p), datetime.date.fromisoformat(d)) for s, k, q, p, d in b["rows"]]
    return spark.createDataFrame(data, b["ddl"])


def create_sql(fq: str, using: str = "") -> list[str]:
    cols = ", ".join(f"{c} {t}" for c, t in SCHEMA_DDL)
    vals = ", ".join(f"({s}, '{k}', {q}, {p}, DATE'{d}')" for s, k, q, p, d in SEED)
    return [f"CREATE TABLE {fq} ({cols}){using}", f"INSERT INTO {fq} VALUES {vals}"]


def _schema(spark, fq: str) -> dict:
    return {f.name: f.dataType.simpleString() for f in spark.table(fq).schema.fields}


def _rows(spark, fq: str) -> list[list]:
    return sorted(_norm(r) for r in spark.table(fq).collect())


def _append(fn) -> dict:
    try:
        fn()
        return {"ok": True}
    except Exception as e:  # noqa: BLE001 - reported, the comparison decides
        return {"ok": False, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:300]}"}


def run_steps(spark, fq: str, using: str = "") -> dict:
    spark.sql(f"DROP TABLE IF EXISTS {fq}")  # a leftover from an interrupted run
    for s in create_sql(fq, using):
        spark.sql(s)
    before = _schema(spark, fq)
    a7 = _append(lambda: _batch(spark, STORE7).writeTo(fq).append())
    a9 = _append(lambda: _batch(spark, STORE9).write.mode("append").saveAsTable(fq))
    rows = _rows(spark, fq)
    return {"rows": len(rows), "schema_before": before, "schema_after": _schema(spark, fq),
            "batch_types": {"store_7": STORE7["ddl"], "store_9": STORE9["ddl"]},
            "appends": {"store_7": {"method": STORE7["method"], **a7}, "store_9": {"method": STORE9["method"], **a9}},
            "rows_after": rows, "by_store": by_store(rows),
            "revenue_total": str(sum(q * Decimal(p) for _, _, q, p, _ in rows))}


def run_uc(spark, catalog: str, schema: str) -> dict:
    fq = f"{catalog}.{schema}.{TABLE}"
    out = run_steps(spark, fq)
    out["operations"] = [r.operation for r in spark.sql(f"DESCRIBE HISTORY {fq}").orderBy("version").collect()]
    return out


def run_oss_spark(spark) -> dict:
    """Local contrast in open-source Spark 4.3 without Delta (a parquet session-catalog table): saveAsTable append
    casts by store assignment, but writeTo() refuses a v1 table, so store 7's batch is not written."""
    return run_steps(spark, TABLE, using=" USING parquet")


def main(argv: list[str]) -> int:
    if argv:  # serverless job: WORKDIR CATALOG SCHEMA
        import pyspark
        from pyspark.sql import SparkSession
        out = run_uc(SparkSession.builder.getOrCreate(), argv[1], argv[2])
        out["engine"] = f"writeTo().append() / saveAsTable append into Unity Catalog Delta on a Databricks serverless job (pyspark {pyspark.__version__})"
        text = json.dumps(out, sort_keys=True)
        print("DAY44_RESULT " + text)  # read back from the task's logs if the output file can't be read
        Path(argv[0].rstrip("/")).mkdir(parents=True, exist_ok=True)
        Path(argv[0].rstrip("/"), "result.json").write_text(text)
        return 0
    out = {"engine": "pure-Python reference (local) / writeTo().append() and saveAsTable append into Unity Catalog "
                     "Delta on a Databricks serverless job (cloud)", **reference()}
    (Path(__file__).resolve().parent / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("rows", "by_store", "revenue_total")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
