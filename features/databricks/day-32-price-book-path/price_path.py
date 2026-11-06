"""Day 32 (Databricks): switch a basket report between the regular price book and the ad-week price book with
`SET PATH` (Databricks SQL 2026.43, 2026-10-19), without editing the report.

  python price_path.py [OUT.json [CATALOG]]   # local: pure-Python reference, writes result.json
                                              # serverless job: OUT.json is a volume path, CATALOG the UC catalog

Two schemas hold the price books:
  <catalog>.cmf_day32_regular  view shelf_price(sku, product, price) + SQL function line_total(sku, price, qty)
  <catalog>.cmf_day32_ad_week  only line_total(sku, price, qty) with this week's ad deals (no shelf_price of its own)

The report (REPORT_SQL) names `basket`, `shelf_price` and `line_total` without any qualifier. The session path
decides where they come from:
  regular:   SET PATH = system.session, <catalog>.cmf_day32_regular, SYSTEM_PATH
  ad week:   SET PATH = <catalog>.cmf_day32_ad_week, PATH   -> ad-week line_total shadows the regular one,
             shelf_price falls through to the regular schema
  plus three checks: a missing schema on the path is skipped, a one-part name and a duplicate entry are refused and
  leave the path unchanged, and a path without system.session can't see the temp view `basket`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REGULAR, AD_WEEK, MISSING = "cmf_day32_regular", "cmf_day32_ad_week", "cmf_day32_missing"

SHELF = [  # sku, product, regular shelf price
    ("0001", "bananas 1 lb", 0.69), ("0002", "whole milk 1 gal", 3.89), ("0003", "sourdough loaf", 4.99),
    ("0004", "eggs dozen", 3.29), ("0006", "cheddar 8 oz", 4.29), ("0008", "ground coffee 12 oz", 8.99),
    ("0010", "oat milk 64 oz", 4.59), ("0011", "greek yogurt 32 oz", 5.79),
]
BASKETS = [  # basket, sku, qty
    ("B1", "0004", 3), ("B1", "0010", 2), ("B1", "0001", 4), ("B1", "0003", 1),
    ("B2", "0008", 2), ("B2", "0002", 1), ("B2", "0011", 1), ("B2", "0004", 2),
    ("B3", "0006", 2), ("B3", "0001", 6), ("B3", "0010", 1), ("B3", "0008", 1), ("B3", "0002", 2),
]
# Ad-week deals: eggs 2 for $5.00, oat milk 20% off, coffee $1.00 off each. Everything else at shelf price.
AD_DEALS = {"0004": "2 for $5.00", "0010": "20% off", "0008": "$1.00 off"}

REGULAR_LINE_TOTAL = "ROUND(price * qty, 2)"
AD_LINE_TOTAL = """ROUND(CASE sku
    WHEN '0004' THEN FLOOR(qty / 2) * 5.00 + MOD(qty, 2) * price
    WHEN '0010' THEN price * 0.80 * qty
    WHEN '0008' THEN (price - 1.00) * qty
    ELSE price * qty END, 2)"""

REPORT_SQL = """
SELECT b.basket,
       SUM(b.qty)                                   AS units,
       ROUND(SUM(line_total(b.sku, p.price, b.qty)), 2) AS total
FROM basket AS b JOIN shelf_price AS p USING (sku)
GROUP BY b.basket ORDER BY b.basket
"""


def ddl(catalog: str) -> list[str]:
    values = ", ".join(f"('{s}', '{p}', CAST({x} AS DOUBLE))" for s, p, x in SHELF)
    fn = ("CREATE OR REPLACE FUNCTION {cat}.{sch}.line_total(sku STRING, price DOUBLE, qty INT) "
          "RETURNS DOUBLE RETURN {body}")
    return [
        f"CREATE SCHEMA IF NOT EXISTS {catalog}.{REGULAR}",
        f"CREATE SCHEMA IF NOT EXISTS {catalog}.{AD_WEEK}",
        f"CREATE OR REPLACE VIEW {catalog}.{REGULAR}.shelf_price AS "
        f"SELECT * FROM VALUES {values} AS t(sku, product, price)",
        fn.format(cat=catalog, sch=REGULAR, body=REGULAR_LINE_TOTAL),
        fn.format(cat=catalog, sch=AD_WEEK, body=AD_LINE_TOTAL),
    ]


def basket_view_sql() -> str:
    values = ", ".join(f"('{b}', '{s}', {q})" for b, s, q in BASKETS)
    return f"CREATE OR REPLACE TEMPORARY VIEW basket AS SELECT * FROM VALUES {values} AS t(basket, sku, qty)"


# ---------- pure-Python reference ----------
def _line(sku: str, price: float, qty: int, ad: bool) -> float:
    if ad and sku == "0004":
        return round((qty // 2) * 5.00 + (qty % 2) * price, 2)
    if ad and sku == "0010":
        return round(price * 0.80 * qty, 2)
    if ad and sku == "0008":
        return round((price - 1.00) * qty, 2)
    return round(price * qty, 2)


def report_reference(ad: bool) -> list[dict]:
    price = {s: x for s, _, x in SHELF}
    out = []
    for b in sorted({b for b, _, _ in BASKETS}):
        lines = [(s, q) for bb, s, q in BASKETS if bb == b]
        out.append({"basket": b, "units": sum(q for _, q in lines),
                    "total": round(sum(_line(s, price[s], q, ad) for s, q in lines), 2)})
    return out


def summarise(regular: list[dict], ad: list[dict], paths: dict, checks: dict) -> dict:
    savings = [{"basket": r["basket"], "regular": r["total"], "ad_week": a["total"],
                "saved": round(r["total"] - a["total"], 2)} for r, a in zip(regular, ad)]
    return {"baskets": len(regular), "units": sum(r["units"] for r in regular),
            "regular_total": round(sum(r["total"] for r in regular), 2),
            "ad_week_total": round(sum(a["total"] for a in ad), 2),
            "saved": round(sum(s["saved"] for s in savings), 2),
            "by_basket": savings, "deals": AD_DEALS, "paths": paths, "checks": checks}


def expected_paths(catalog: str) -> dict:
    reg, adw, miss = f"{catalog}.{REGULAR}", f"{catalog}.{AD_WEEK}", f"{catalog}.{MISSING}"
    return {"regular": f"system.session,{reg},system.builtin,system.ai",
            "ad_week": f"{adw},system.session,{reg},system.builtin,system.ai",
            "with_missing": f"{miss},{adw},system.session,{reg},system.builtin,system.ai"}


def normalise_path(p: str, catalog: str) -> str:
    """Spark prints SYSTEM_PATH as system.builtin (+ system.ai on Databricks); compare without the AI schema and with
    a neutral catalog name."""
    parts = [x.strip().replace("`", "") for x in p.split(",")]
    parts = [x for x in parts if x and x != "system.ai"]
    return ",".join(x.replace(f"{catalog}.", "<catalog>.", 1) for x in parts)


# ---------- Spark (local pyspark 4.3 with spark.sql.path.enabled, or a Databricks serverless job) ----------
def _err_class(e: Exception) -> str:
    text = str(e)
    for name in ("INVALID_SQL_PATH_SCHEMA_REFERENCE", "DUPLICATE_SQL_PATH_ENTRY", "TABLE_OR_VIEW_NOT_FOUND"):
        if name in text:
            return name
    return text.splitlines()[0][:120] if text else type(e).__name__


def run_spark(spark, catalog: str) -> dict:
    for stmt in ddl(catalog):
        spark.sql(stmt)
    spark.sql(basket_view_sql())
    reg, adw, miss = f"{catalog}.{REGULAR}", f"{catalog}.{AD_WEEK}", f"{catalog}.{MISSING}"

    def path() -> str:
        return normalise_path(spark.sql("SELECT current_path() AS p").first().p, catalog)

    def report() -> list[dict]:
        return [{"basket": r.basket, "units": int(r.units), "total": round(float(r.total), 2)}
                for r in spark.sql(REPORT_SQL).collect()]

    spark.sql(f"SET PATH = system.session, {reg}, SYSTEM_PATH")
    paths, regular = {"regular": path()}, report()
    spark.sql(f"SET PATH = {adw}, PATH")                     # extend the path, the ad week first
    paths["ad_week"], ad = path(), report()
    spark.sql(f"SET PATH = {miss}, PATH")                    # a schema that doesn't exist is skipped
    paths["with_missing"], ad_again = path(), report()
    checks = {"missing_schema_skipped": ad_again == ad}
    for name, stmt in (("one_part_name", f"SET PATH = {REGULAR}, PATH"),
                       ("duplicate_entry", "SET PATH = SYSTEM_PATH, system.builtin")):
        before = path()
        try:
            spark.sql(stmt)
            checks[name] = "accepted"
        except Exception as e:  # noqa: BLE001 - the refusal is the expected outcome
            checks[name] = _err_class(e)
        checks[f"{name}_path_unchanged"] = path() == before
    spark.sql(f"SET PATH = {reg}, SYSTEM_PATH")              # no system.session: the temp view is out of reach
    try:
        spark.sql(REPORT_SQL).collect()
        checks["no_session_schema"] = "found basket"
    except Exception as e:  # noqa: BLE001
        checks["no_session_schema"] = _err_class(e)
    spark.sql("SET PATH = DEFAULT_PATH")
    return summarise(regular, ad, paths, checks)


EXPECTED_CHECKS = {"missing_schema_skipped": True,
                   "one_part_name": "INVALID_SQL_PATH_SCHEMA_REFERENCE", "one_part_name_path_unchanged": True,
                   "duplicate_entry": "DUPLICATE_SQL_PATH_ENTRY", "duplicate_entry_path_unchanged": True,
                   "no_session_schema": "TABLE_OR_VIEW_NOT_FOUND"}


def reference() -> dict:
    paths = {k: normalise_path(v, "<catalog>") for k, v in expected_paths("<catalog>").items()}
    return summarise(report_reference(False), report_reference(True), paths, dict(EXPECTED_CHECKS))


def drop_sql(catalog: str) -> list[str]:
    return [f"DROP SCHEMA IF EXISTS {catalog}.{AD_WEEK} CASCADE", f"DROP SCHEMA IF EXISTS {catalog}.{REGULAR} CASCADE"]


def run_cloud_job(catalog: str) -> dict:
    import pyspark
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    try:
        spark.conf.set("spark.sql.path.enabled", "true")   # needed on open-source Spark 4.3; on by default on Databricks
    except Exception:  # noqa: BLE001 - serverless may refuse to set it, which is fine
        pass
    try:
        out = run_spark(spark, catalog)
    finally:
        for stmt in drop_sql(catalog):
            try:
                spark.sql(stmt)
            except Exception:  # noqa: BLE001 - run_cloud.py also drops the schemas
                pass
    out["engine"] = f"SET PATH on a Databricks serverless job (pyspark {pyspark.__version__})"
    return out


def main(argv: list[str]) -> int:
    if argv:  # serverless job: argv[0] is a Unity Catalog volume path, argv[1] the catalog
        text = json.dumps(run_cloud_job(argv[1] if len(argv) > 1 else "workspace"), sort_keys=True)
        print("DAY32_RESULT " + text)    # read back from the task's logs if the output file can't be read
        Path(argv[0]).write_text(text)
        return 0
    out = {"engine": "pure-Python reference (local) / SET PATH in Spark SQL on a Databricks serverless job (cloud)",
           **reference()}
    (Path(__file__).resolve().parent / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("baskets", "units", "regular_total", "ad_week_total", "saved")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
