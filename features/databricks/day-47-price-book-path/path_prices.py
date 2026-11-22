"""Day 47 (Databricks): one unqualified basket-pricing query, three price books, switched with **SET PATH**
(Databricks SQL 2026.43 Preview, release note 2026-10-19, "Control name resolution with SET PATH"; Databricks
Runtime 19 and above). `current_path()` shows the path in force.

A store keeps its everyday prices in `live.shelf_prices` and the weekend promotion as a full copy in
`promo.shelf_prices`; the basket lives only in `live`. The report query never names a schema:

    SELECT sum(b.qty * p.price_cents) FROM basket b JOIN shelf_prices p USING (sku)

  SET PATH = SYSTEM_PATH, <cat>.live                  -> everyday total (basket and prices from live)
  SET PATH = SYSTEM_PATH, <cat>.promo, <cat>.live     -> promo total (promo's shelf_prices shadows live's,
                                                         basket still found in live, the next path entry)
  SET PATH = SYSTEM_PATH, <cat>.not_there, <cat>.live -> everyday total (a missing schema is skipped)
  CREATE VIEW live.basket_value under the promo path, then SET PATH back to live:
                                                       the view still prices at promo (it captured its path),
                                                       the same query typed directly prices at everyday.
  SET PATH = live              -> INVALID_SQL_PATH_SCHEMA_REFERENCE (one-part name), path unchanged
  SET PATH = SYSTEM_PATH, system.builtin -> DUPLICATE_SQL_PATH_ENTRY, path unchanged

  python path_prices.py                     # local: Python name-resolution model + DuckDB search_path; result.json
  python path_prices.py WORKDIR CATALOG     # serverless job: runs the steps in Unity Catalog, result to WORKDIR
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE, PROMO, MISSING = "cmf_day47_live", "cmf_day47_promo", "cmf_day47_not_there"
BASKET = [("MILK-1L", 2), ("BREAD-WHITE", 1), ("EGGS-12", 1), ("COFFEE-BEANS", 1), ("BANANA", 6), ("CHOC-BAR", 3)]
PRICES = {  # sku -> (everyday cents, weekend promo cents)
    "MILK-1L": (129, 129), "BREAD-WHITE": (249, 199), "EGGS-12": (389, 389),
    "COFFEE-BEANS": (749, 599), "BANANA": (25, 20), "CHOC-BAR": (85, 60),
}
QUERY = "SELECT sum(b.qty * p.price_cents) AS total FROM basket b JOIN shelf_prices p USING (sku)"
SYSTEM = ["system.builtin", "system.ai"]
SCENARIOS = ("live", "promo_first", "missing_skipped", "view_captured", "direct_after_view")


# ---------- pure-Python model of Databricks name resolution (path in order, missing schemas skipped)
def schemas(catalog: str) -> dict[str, dict[str, object]]:
    live_prices = {s: p[0] for s, p in PRICES.items()}
    promo_prices = {s: p[1] for s, p in PRICES.items()}
    return {f"{catalog}.{LIVE}": {"basket": BASKET, "shelf_prices": live_prices},
            f"{catalog}.{PROMO}": {"shelf_prices": promo_prices}}


def resolve(path: list[str], name: str, catalog: str):
    for entry in path:
        tables = schemas(catalog).get(entry)
        if tables is not None and name in tables:
            return tables[name]
    raise LookupError(f"TABLE_OR_VIEW_NOT_FOUND {name}")


def total(path: list[str], catalog: str) -> int:
    basket, prices = resolve(path, "basket", catalog), resolve(path, "shelf_prices", catalog)
    return sum(q * prices[s] for s, q in basket)


def paths(catalog: str) -> dict[str, list[str]]:
    live, promo, missing = f"{catalog}.{LIVE}", f"{catalog}.{PROMO}", f"{catalog}.{MISSING}"
    return {"live": SYSTEM + [live], "promo_first": SYSTEM + [promo, live],
            "missing_skipped": SYSTEM + [missing, live],
            "view_captured": SYSTEM + [live],      # path in force when the view is queried
            "direct_after_view": SYSTEM + [live]}


def reference(catalog: str = "workspace") -> dict:
    p = paths(catalog)
    totals = {k: total(v, catalog) for k, v in p.items()}
    totals["view_captured"] = total(p["promo_first"], catalog)  # the view keeps the path it was created under
    return {"totals": totals, "current_path": {k: ",".join(v) for k, v in p.items()},
            "errors": {"one_part": "INVALID_SQL_PATH_SCHEMA_REFERENCE", "duplicate": "DUPLICATE_SQL_PATH_ENTRY"},
            "path_unchanged_after_errors": True}


def duckdb_totals() -> dict[str, int]:
    """DuckDB's search_path as an independent engine for the two plain scenarios (DuckDB rejects a missing schema
    in search_path, where Databricks skips it, so that scenario is the Python model's alone)."""
    import duckdb
    con = duckdb.connect()
    for s in ("live", "promo"):
        con.execute(f"CREATE SCHEMA {s}; CREATE TABLE {s}.shelf_prices (sku VARCHAR, price_cents INTEGER)")
    con.execute("CREATE TABLE live.basket (sku VARCHAR, qty INTEGER)")
    con.executemany("INSERT INTO live.basket VALUES (?, ?)", BASKET)
    con.executemany("INSERT INTO live.shelf_prices VALUES (?, ?)", [(s, p[0]) for s, p in PRICES.items()])
    con.executemany("INSERT INTO promo.shelf_prices VALUES (?, ?)", [(s, p[1]) for s, p in PRICES.items()])
    out = {}
    for key, sp in (("live", "live"), ("promo_first", "promo,live")):
        con.execute(f"SET search_path = '{sp}'")
        out[key] = int(con.execute(QUERY).fetchall()[0][0])
    return out


# ---------- Databricks (serverless job, one Spark session so SET PATH holds between statements)
def run_uc(spark, catalog: str) -> dict:
    live, promo, missing = f"{catalog}.{LIVE}", f"{catalog}.{PROMO}", f"{catalog}.{MISSING}"
    for s in (live, promo):
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {s}")
    for t in (f"{live}.basket", f"{live}.shelf_prices", f"{promo}.shelf_prices"):
        spark.sql(f"DROP TABLE IF EXISTS {t}")
    spark.sql(f"DROP VIEW IF EXISTS {live}.basket_value")
    vals = ", ".join(f"('{s}', {q})" for s, q in BASKET)
    spark.sql(f"CREATE TABLE {live}.basket AS SELECT * FROM VALUES {vals} AS t(sku, qty)")
    for schema, i in ((live, 0), (promo, 1)):
        vals = ", ".join(f"('{s}', {p[i]})" for s, p in PRICES.items())
        spark.sql(f"CREATE TABLE {schema}.shelf_prices AS SELECT * FROM VALUES {vals} AS t(sku, price_cents)")

    def run(path_sql: str) -> tuple[int, str]:
        spark.sql(f"SET PATH = {path_sql}")
        cp = spark.sql("SELECT current_path() AS p").collect()[0]["p"]
        return int(spark.sql(QUERY).collect()[0]["total"]), cp

    totals, cps = {}, {}
    totals["live"], cps["live"] = run(f"SYSTEM_PATH, {live}")
    totals["missing_skipped"], cps["missing_skipped"] = run(f"SYSTEM_PATH, {missing}, {live}")
    totals["promo_first"], cps["promo_first"] = run(f"SYSTEM_PATH, {promo}, {live}")
    spark.sql(f"CREATE VIEW {live}.basket_value AS {QUERY}")   # created under the promo path
    spark.sql(f"SET PATH = SYSTEM_PATH, {live}")
    cps["view_captured"] = cps["direct_after_view"] = spark.sql("SELECT current_path() AS p").collect()[0]["p"]
    totals["view_captured"] = int(spark.sql(f"SELECT total FROM {live}.basket_value").collect()[0]["total"])
    totals["direct_after_view"] = int(spark.sql(QUERY).collect()[0]["total"])

    errors, before = {}, cps["direct_after_view"]
    for key, bad in (("one_part", LIVE), ("duplicate", "SYSTEM_PATH, system.builtin")):
        try:
            spark.sql(f"SET PATH = {bad}").collect()
            errors[key] = "accepted"
        except Exception as e:  # noqa: BLE001 - the error class name is the result
            msg = str(e)
            errors[key] = next((c for c in ("INVALID_SQL_PATH_SCHEMA_REFERENCE", "DUPLICATE_SQL_PATH_ENTRY")
                                if c in msg), msg[:160])
    unchanged = spark.sql("SELECT current_path() AS p").collect()[0]["p"] == before
    for t in (f"{live}.basket_value",):
        spark.sql(f"DROP VIEW IF EXISTS {t}")
    return {"totals": totals, "current_path": cps, "errors": errors, "path_unchanged_after_errors": unchanged}


def summarise() -> dict:
    ref = reference("<catalog>")
    t = ref["totals"]
    return {"basket_lines": len(BASKET), "units": sum(q for _, q in BASKET),
            "everyday_cents": t["live"], "promo_cents": t["promo_first"],
            "promo_saving_cents": t["live"] - t["promo_first"], "scenarios": len(SCENARIOS) + 2,
            "reference": ref, "duckdb": duckdb_totals()}


def main(argv: list[str]) -> int:
    if argv:  # serverless job: WORKDIR CATALOG
        import pyspark
        from pyspark.sql import SparkSession
        out = run_uc(SparkSession.builder.getOrCreate(), argv[1])
        out["engine"] = f"SET PATH on a Databricks serverless job (pyspark {pyspark.__version__})"
        text = json.dumps(out, sort_keys=True)
        print("DAY47_RESULT " + text)
        Path(argv[0].rstrip("/")).mkdir(parents=True, exist_ok=True)
        Path(argv[0].rstrip("/"), "result.json").write_text(text)
        return 0
    out = {"engine": "pure-Python name-resolution model + DuckDB search_path (local) / SET PATH on a Databricks "
                     "serverless job (cloud)", **summarise()}
    (HERE / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "reference"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
