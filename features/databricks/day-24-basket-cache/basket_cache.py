"""Day 24 (Databricks): build the week's enriched basket lines once, cache them, and serve three reports from the
cache. Runs as a serverless job task (environment version 5, where df.cache() is now supported) and locally on
pyspark with the same code.

  python basket_cache.py [OUT.json]    # local: writes result.json (or OUT.json)

On serverless compute before 2026-10-23, `df.cache()` / `df.persist()` raised NOT_SUPPORTED_WITH_SERVERLESS, so a
job that reused one expensive DataFrame for several outputs had to recompute it for each, or write it to a table
first. All amounts are integer cents, so the cloud and local results can be compared exactly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

LINES = 120_000  # candidate scan lines; about half are kept (baskets of 2-7 lines)
STORES = ["east", "west", "north", "hq-express"]
# sku, name, category, price in cents
PRICES = [
    (0, "whole milk 1 gal", "dairy", 389), (1, "oat milk 64 oz", "dairy", 449), (2, "greek yogurt", "dairy", 129),
    (3, "cheddar 8 oz", "dairy", 399), (4, "bananas lb", "produce", 59), (5, "apples 3 lb", "produce", 499),
    (6, "spinach 10 oz", "produce", 349), (7, "avocado", "produce", 125), (8, "sourdough loaf", "bakery", 549),
    (9, "bagels 6 pk", "bakery", 429), (10, "eggs dozen", "dairy", 329), (11, "chicken breast lb", "meat", 449),
    (12, "ground beef lb", "meat", 599), (13, "salmon fillet", "meat", 1099), (14, "pasta 1 lb", "pantry", 179),
    (15, "rice 2 lb", "pantry", 299), (16, "olive oil", "pantry", 899), (17, "coffee 12 oz", "pantry", 999),
    (18, "cola 12 pk", "drinks", 749), (19, "sparkling water", "drinks", 499), (20, "orange juice", "drinks", 429),
    (21, "ice cream pint", "frozen", 549), (22, "frozen pizza", "frozen", 699), (23, "peas 1 lb", "frozen", 199),
]


def enriched_lines(spark: SparkSession) -> DataFrame:
    """The 'expensive' part every report needs: generate scan lines, drop voids, join prices, compute totals."""
    prices = spark.createDataFrame(PRICES, "sku long, name string, category string, price_cents long")
    stores = F.array(*[F.lit(s) for s in STORES])
    lines = (
        spark.range(LINES)
        .select(F.col("id"), (F.col("id") / 7).cast("long").alias("basket_id"))
        .where((F.col("id") % 7) < 2 + (F.col("basket_id") % 6))   # baskets of 2..7 lines
        .where((F.col("id") % 97) != 0)                              # voided scans
        .select(
            "basket_id",
            F.element_at(stores, (F.col("basket_id") % len(STORES) + 1).cast("int")).alias("store"),
            ((F.col("id") * 7 + F.col("basket_id") * 3) % len(PRICES)).alias("sku"),
            (F.col("id") % 3 + 1).alias("qty"),
        )
    )
    return lines.join(F.broadcast(prices), "sku").withColumn("line_cents", F.col("qty") * F.col("price_cents"))


def reports(df: DataFrame) -> dict:
    by_store = (df.groupBy("store").agg(F.countDistinct("basket_id").alias("baskets"), F.sum("qty").alias("items"),
                                        F.sum("line_cents").alias("revenue_cents")).orderBy("store").collect())
    by_category = (df.groupBy("category").agg(F.sum("line_cents").alias("revenue_cents"))
                   .orderBy(F.desc("revenue_cents"), "category").collect())
    sizes = (df.groupBy("basket_id").count().groupBy(F.col("count").alias("lines")).count()
             .orderBy("lines").collect())
    return {
        "by_store": [{"store": r["store"], "baskets": int(r["baskets"]), "items": int(r["items"]),
                      "revenue_cents": int(r["revenue_cents"])} for r in by_store],
        "by_category": [{"category": r["category"], "revenue_cents": int(r["revenue_cents"])} for r in by_category],
        "basket_sizes": {str(int(r["lines"])): int(r["count"]) for r in sizes},
    }


def run(spark: SparkSession) -> dict:
    df = enriched_lines(spark)
    before = df.is_cached
    df.cache()                       # new on serverless (environment version 5+)
    rows = df.count()                # materialises the cache
    level = str(df.storageLevel)
    cached = df.is_cached
    out = reports(df)                # three reports, all served from the cached lines
    df.unpersist()
    return {
        "cache": {"cached_before": before, "cached_after_cache": cached, "storage_level": level,
                  "cached_after_unpersist": df.is_cached, "rows_cached": int(rows), "reports_from_cache": 3},
        "reports": out,
    }


def main(argv: list[str]) -> int:
    spark = SparkSession.builder.getOrCreate()
    result = run(spark)
    text = json.dumps(result, sort_keys=True)
    print("DAY24_RESULT " + text)    # read back from the task's logs if the output file can't be read
    if argv:                         # the job passes a Unity Catalog volume path
        Path(argv[0]).write_text(text)
    else:                            # local run: refresh result.json
        engine = "pyspark local (local) / serverless job, environment version 5 (cloud)"
        (Path(__file__).resolve().parent / "result.json").write_text(
            json.dumps({"engine": engine, **result}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
