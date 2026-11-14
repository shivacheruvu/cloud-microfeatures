"""Day 40 (Databricks): a store-day sales rollup as a **standalone materialized view created and refreshed from
serverless general compute** (GA, 2026-11-11): `CREATE MATERIALIZED VIEW` and `REFRESH MATERIALIZED VIEW` sent with
`spark.sql` from a serverless job, with no SQL warehouse and no pipeline to set up.

  python store_day_mv.py                          # local reference: the rollup before and after the late receipts
                                                  # (plain Python); writes result.json
  python store_day_mv.py CATALOG SCHEMA OUTDIR    # serverless job: table, CREATE MV, read, late receipts, read
                                                  # (still the old answer), REFRESH, read; writes OUTDIR/result.json

The story: till receipts land in a sales table; the morning report reads a materialized view of units and revenue per
store per day. Late receipts arrive (a store's feed was delayed, plus a new day). Until the view is refreshed it keeps
answering with what it materialized; after a synchronous REFRESH it equals the new rollup. run_cloud.py requires the
three reads to equal the local reference.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLE, VIEW = "till_receipts", "store_day_sales"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
COLUMNS = ("store", "sale_date", "receipt", "sku", "qty", "price_cents")

# (store, sale_date, receipt, sku, qty, price_cents)
BATCH_1 = [
    ("S01", "2026-11-10", "R1001", "MILK-2L", 2, 349),
    ("S01", "2026-11-10", "R1001", "BREAD-WW", 1, 279),
    ("S01", "2026-11-10", "R1002", "EGGS-12", 1, 425),
    ("S01", "2026-11-11", "R1010", "BANANA-KG", 3, 129),
    ("S01", "2026-11-11", "R1011", "MILK-2L", 1, 349),
    ("S02", "2026-11-10", "R2001", "COFFEE-500", 1, 899),
    ("S02", "2026-11-10", "R2001", "MILK-2L", 1, 349),
    ("S02", "2026-11-11", "R2005", "EGGS-12", 2, 425),
    ("S03", "2026-11-10", "R3001", "BREAD-WW", 2, 279),
    ("S03", "2026-11-10", "R3002", "APPLE-KG", 2, 299),
]
# Late receipts: S03's 11-11 feed arrived a day late, S02 had two more 11-11 sales, and 11-12 opened.
BATCH_2 = [
    ("S03", "2026-11-11", "R3010", "MILK-2L", 3, 349),
    ("S03", "2026-11-11", "R3010", "COFFEE-500", 1, 899),
    ("S02", "2026-11-11", "R2006", "BANANA-KG", 2, 129),
    ("S02", "2026-11-11", "R2007", "BREAD-WW", 1, 279),
    ("S01", "2026-11-12", "R1020", "EGGS-12", 1, 425),
    ("S01", "2026-11-12", "R1020", "APPLE-KG", 1, 299),
]

ROLLUP_SQL = (f"SELECT store, sale_date, COUNT(DISTINCT receipt) AS receipts, CAST(SUM(qty) AS BIGINT) AS units, "
              f"CAST(SUM(qty * price_cents) AS BIGINT) AS revenue_cents FROM {{table}} GROUP BY store, sale_date")


def rollup(rows: list[tuple]) -> list[dict]:
    """Units, receipts and revenue (cents) per store per day, sorted: what the view must hold."""
    acc: dict[tuple, dict] = {}
    for store, day, receipt, _sku, qty, cents in rows:
        a = acc.setdefault((store, day), {"store": store, "sale_date": day, "receipts": set(), "units": 0,
                                          "revenue_cents": 0})
        a["receipts"].add(receipt)
        a["units"] += qty
        a["revenue_cents"] += qty * cents
    return [{**a, "receipts": len(a["receipts"])} for _, a in sorted(acc.items())]


def totals(view: list[dict]) -> dict:
    return {"store_days": len(view), "units": sum(r["units"] for r in view),
            "revenue_cents": sum(r["revenue_cents"] for r in view)}


def reference() -> dict:
    before, after = rollup(BATCH_1), rollup(BATCH_1 + BATCH_2)
    changed = sorted({(r["store"], r["sale_date"]) for r in after} - {(r["store"], r["sale_date"]) for r in before}
                     | {(r["store"], r["sale_date"]) for r in after if r not in before})
    return {"rows_batch_1": len(BATCH_1), "rows_batch_2": len(BATCH_2), "before_refresh": before,
            "after_refresh": after, "totals_before": totals(before), "totals_after": totals(after),
            "store_days_changed_by_refresh": [f"{s} {d}" for s, d in changed]}


def fqn(*parts: str) -> str:
    for part in parts:
        if not NAME.match(part or ""):
            raise ValueError(f"bad identifier {part!r}")
    return ".".join(f"`{p}`" for p in parts)


def values_sql(rows: list[tuple]) -> str:
    for r in rows:  # everything goes into literals: keep to the characters the data uses
        if not all(re.match(r"^[A-Z0-9-]+$", str(v)) for v in r[:4]) or not all(isinstance(v, int) for v in r[4:]):
            raise ValueError(f"unexpected value in {r}")
    return ", ".join(f"('{s}', DATE'{d}', '{rc}', '{k}', {q}, {c})" for s, d, rc, k, q, c in rows)


def statements(catalog: str, schema: str) -> dict:
    table, view = fqn(catalog, schema, TABLE), fqn(catalog, schema, VIEW)
    return {
        "create_table": f"CREATE OR REPLACE TABLE {table} (store STRING, sale_date DATE, receipt STRING, sku STRING, "
                        f"qty INT, price_cents INT)",
        "insert_1": f"INSERT INTO {table} VALUES {values_sql(BATCH_1)}",
        "create_view": f"CREATE OR REPLACE MATERIALIZED VIEW {view} "
                       f"COMMENT 'cloud-microfeatures day 40: units and revenue per store per day' "
                       f"AS {ROLLUP_SQL.format(table=table)}",
        "insert_2": f"INSERT INTO {table} VALUES {values_sql(BATCH_2)}",
        "refresh_view": f"REFRESH MATERIALIZED VIEW {view}",
        "read_view": f"SELECT store, CAST(sale_date AS STRING) AS sale_date, CAST(receipts AS INT) AS receipts, "
                     f"CAST(units AS BIGINT) AS units, CAST(revenue_cents AS BIGINT) AS revenue_cents FROM {view} "
                     f"ORDER BY store, sale_date",
        "table_type": f"SELECT table_type FROM `{catalog}`.information_schema.tables "
                      f"WHERE table_schema = '{schema}' AND table_name = '{VIEW}'",
    }


def job(catalog: str, schema: str, outdir: str) -> dict:
    """Runs on Databricks serverless (environment version 6 or later: standalone MVs need Runtime 18.1+)."""
    import time

    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    sql = statements(catalog, schema)

    def read() -> list[dict]:
        return [{k: (int(v) if k in ("receipts", "units", "revenue_cents") else v) for k, v in r.asDict().items()}
                for r in spark.sql(sql["read_view"]).collect()]

    res: dict = {"engine": f"Databricks serverless general compute (Spark {spark.version})"}
    spark.sql(sql["create_table"])
    spark.sql(sql["insert_1"])
    t = time.time()
    spark.sql(sql["create_view"])
    res["create_seconds"] = round(time.time() - t, 1)
    res["before_refresh"] = read()
    spark.sql(sql["insert_2"])
    res["stale_before_refresh"] = read()  # a materialized view keeps its answer until it is refreshed
    t = time.time()
    spark.sql(sql["refresh_view"])
    res["refresh_seconds"] = round(time.time() - t, 1)
    res["after_refresh"] = read()
    res["table_type"] = next((r[0] for r in spark.sql(sql["table_type"]).collect()), None)
    Path(outdir).mkdir(parents=True, exist_ok=True)
    (Path(outdir) / "result.json").write_text(json.dumps(res))
    print("DAY40_RESULT " + json.dumps(res))
    return res


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        job(*argv[1:4])
        return 0
    ref = reference()
    (HERE / "result.json").write_text(json.dumps({"engine": "plain Python (local) / Databricks serverless "
                                                            "materialized view (cloud)", **ref}, indent=2) + "\n")
    print(json.dumps({k: ref[k] for k in ("totals_before", "totals_after", "store_days_changed_by_refresh")},
                     indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
