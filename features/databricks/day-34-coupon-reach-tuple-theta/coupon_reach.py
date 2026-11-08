"""Day 34 (Databricks): measure a coupon e-mail's reach and the spend behind it by combining **tuple sketches** of
loyalty spend with a **theta sketch** of e-mailed customers, using `tuple_intersection_theta_double`,
`tuple_difference_theta_double` and `tuple_union_theta_double` (Databricks SQL 2026.15, 2026-05-26).

  python coupon_reach.py [WORKDIR]    # local: pure-Python reference, writes result.json
                                      # serverless job: WORKDIR is a Unity Catalog volume folder for the result

A tuple sketch keeps distinct keys (customers) *with a summary* (here: spend in cents, summed); a theta sketch keeps
distinct keys only. The marketing team ships just a theta sketch of who got the e-mail (no spend, no raw IDs to
join), and each store keeps a tuple sketch of who bought and for how much. The new functions combine the two
directly:
  tuple_intersection_theta_double(spend, emailed)  -> buyers who got the e-mail, and their spend
  tuple_difference_theta_double(spend, emailed)    -> buyers the e-mail missed, and their spend
  tuple_union_theta_double(spend, emailed)         -> everyone touched (buyers or e-mailed); e-mail-only keys get 0.0
Per store, and chain-wide after tuple_union_agg_double merges the store sketches. With fewer keys than the sketch's
nominal entries (2^12) the sketches are exact, so the Spark result must equal the plain-Python set arithmetic.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORES = ("S1", "S2", "S3")
CUSTOMERS, EMAIL_ONLY = 600, 50          # C0001..C0600 shop; C0601..C0650 are e-mailed but never shopped
LG_K = 12


def purchases() -> list[tuple[str, str, int]]:
    """Deterministic loyalty purchases (customer_id, store, spend_cents): 1,800 rows over 3 stores."""
    rows, x = [], 20261107
    for _ in range(1800):
        x = (x * 1103515245 + 12345) % 2**31
        cust = 1 + x % CUSTOMERS
        x = (x * 1103515245 + 12345) % 2**31
        store = STORES[(x >> 8) % 3]
        x = (x * 1103515245 + 12345) % 2**31
        cents = 199 + x % 4800
        rows.append((f"C{cust:04d}", store, cents))
    return rows


def emailed() -> list[str]:
    """Customers the coupon e-mail went to: every customer whose number is divisible by 3, plus 50 who never shop."""
    return [f"C{c:04d}" for c in range(1, CUSTOMERS + 1) if c % 3 == 0] + \
           [f"C{c:04d}" for c in range(CUSTOMERS + 1, CUSTOMERS + EMAIL_ONLY + 1)]


def _figures(spend: dict[str, int], mail: set[str]) -> dict:
    reached = {c: v for c, v in spend.items() if c in mail}
    missed = {c: v for c, v in spend.items() if c not in mail}
    return {"buyers": len(spend), "spend_cents": sum(spend.values()),
            "reached_buyers": len(reached), "reached_spend_cents": sum(reached.values()),
            "missed_buyers": len(missed), "missed_spend_cents": sum(missed.values()),
            "touched": len(set(spend) | mail), "touched_spend_cents": sum(spend.values())}


def reference() -> dict:
    rows, mail = purchases(), set(emailed())
    by_store: dict[str, dict[str, int]] = {s: {} for s in STORES}
    chain: dict[str, int] = {}
    for c, s, v in rows:
        by_store[s][c] = by_store[s].get(c, 0) + v
        chain[c] = chain.get(c, 0) + v
    stores = [{"store": s, **_figures(by_store[s], mail)} for s in STORES]
    return {"purchases": len(rows), "emailed": len(mail), "stores": stores, "chain": _figures(chain, mail)}


# ---------- Spark (local pyspark 4.2+, or a Databricks serverless job) ----------
STORE_SKETCHES_SQL = f"""
SELECT store, tuple_sketch_agg_double(customer_id, CAST(spend_cents AS DOUBLE), {LG_K}, 'sum') AS spend
FROM purchases GROUP BY store
"""
EMAILED_SKETCH_SQL = f"SELECT theta_sketch_agg(customer_id, {LG_K}) AS mail FROM emailed"
FIGURES = """
  CAST(tuple_sketch_estimate_double({s}) AS BIGINT)                                         AS buyers,
  CAST(tuple_sketch_summary_double({s}, 'sum') AS BIGINT)                                   AS spend_cents,
  CAST(tuple_sketch_estimate_double(tuple_intersection_theta_double({s}, mail, 'sum')) AS BIGINT) AS reached_buyers,
  CAST(tuple_sketch_summary_double(tuple_intersection_theta_double({s}, mail, 'sum'), 'sum') AS BIGINT)
                                                                                              AS reached_spend_cents,
  CAST(tuple_sketch_estimate_double(tuple_difference_theta_double({s}, mail)) AS BIGINT)    AS missed_buyers,
  CAST(tuple_sketch_summary_double(tuple_difference_theta_double({s}, mail), 'sum') AS BIGINT)
                                                                                              AS missed_spend_cents,
  CAST(tuple_sketch_estimate_double(tuple_union_theta_double({s}, mail, {k}, 'sum')) AS BIGINT) AS touched,
  CAST(tuple_sketch_summary_double(tuple_union_theta_double({s}, mail, {k}, 'sum'), 'sum') AS BIGINT)
                                                                                              AS touched_spend_cents
"""
STORE_SQL = "SELECT store," + FIGURES.format(s="spend", k=LG_K) + \
            "FROM store_sketches CROSS JOIN emailed_sketch ORDER BY store"
CHAIN_SQL = "SELECT" + FIGURES.format(s="chain", k=LG_K) + \
            f"FROM (SELECT tuple_union_agg_double(spend, {LG_K}, 'sum') AS chain FROM store_sketches) " \
            "CROSS JOIN emailed_sketch"
FIELDS = ("buyers", "spend_cents", "reached_buyers", "reached_spend_cents", "missed_buyers", "missed_spend_cents",
          "touched", "touched_spend_cents")


def run_spark(spark) -> dict:
    spark.createDataFrame(purchases(), "customer_id string, store string, spend_cents int") \
        .createOrReplaceTempView("purchases")
    spark.createDataFrame([(c,) for c in emailed()], "customer_id string").createOrReplaceTempView("emailed")
    # sketches are what each side would store and ship: one binary per store, one for the e-mail list
    spark.sql(STORE_SKETCHES_SQL).createOrReplaceTempView("store_sketches")
    spark.sql(EMAILED_SKETCH_SQL).createOrReplaceTempView("emailed_sketch")
    stores = [{"store": r["store"], **{f: int(r[f]) for f in FIELDS}} for r in spark.sql(STORE_SQL).collect()]
    c = spark.sql(CHAIN_SQL).first()
    return {"purchases": spark.table("purchases").count(), "emailed": spark.table("emailed").count(),
            "stores": stores, "chain": {f: int(c[f]) for f in FIELDS}}


def run_cloud_job(workdir: str) -> dict:
    import pyspark
    from pyspark.sql import SparkSession
    out = run_spark(SparkSession.builder.getOrCreate())
    out["engine"] = f"tuple x theta sketch functions on a Databricks serverless job (pyspark {pyspark.__version__})"
    return out


def main(argv: list[str]) -> int:
    if argv:  # serverless job: argv[0] is a Unity Catalog volume folder
        text = json.dumps(run_cloud_job(argv[0].rstrip("/")), sort_keys=True)
        print("DAY34_RESULT " + text)    # read back from the task's logs if the output file can't be read
        Path(argv[0].rstrip("/")).mkdir(parents=True, exist_ok=True)
        Path(argv[0].rstrip("/"), "result.json").write_text(text)
        return 0
    out = {"engine": "pure-Python set arithmetic (local reference) / tuple x theta sketch functions in Spark SQL "
                     "(local pyspark, Databricks serverless job)", **reference()}
    (HERE / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out["chain"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
