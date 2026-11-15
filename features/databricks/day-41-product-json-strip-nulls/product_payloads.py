"""Day 41 (Databricks): slim Open Food Facts-style product payloads with `variant_strip_nulls` (serverless compute,
Databricks Runtime 19 updates, 2026-09-01) before storing them, and measure field completeness on what's left.

  python product_payloads.py [WORKDIR]   # local: pure-Python reference, writes result.json
                                         # serverless job: WORKDIR is a Unity Catalog volume folder for the result

A product exporter writes every known key for every product, so most payloads are padded with JSON nulls (an empty
nutriment, a packaging block with nothing in it, null placeholders in the label list). Steps (the same in local
pyspark 4.3 and on a Databricks serverless job):
  1. parse_json the 8 payloads into a VARIANT column
  2. variant_strip_nulls(payload)          -> object fields and array elements that are variant null are removed
  3. variant_strip_nulls(payload, false)   -> same, but null array elements are kept (label positions survive)
  4. per product: JSON bytes before / after, filled nutriments, labels, and whether the packaging block is now empty
Every stripped payload must equal a pure-Python strip of the same JSON, and every number the reference.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PRODUCTS = [  # code, payload as the exporter writes it
    ("0001", {"name": "Whole milk 1 gal", "brand": "Harbor Farms", "category": "dairy",
              "nutriments": {"energy_kcal": 150, "fat": 8, "sugars": 12, "salt": 0.3, "fiber": None},
              "labels": ["vitamin-d", None, "rbst-free"], "packaging": {"material": "hdpe", "recyclable": True}}),
    ("0002", {"name": "Sourdough loaf", "brand": None, "category": "bakery",
              "nutriments": {"energy_kcal": 240, "fat": 1.5, "sugars": None, "salt": 1.1, "fiber": 2},
              "labels": [None, None], "packaging": {"material": None, "recyclable": None}}),
    ("0003", {"name": "Bananas per lb", "brand": None, "category": "produce",
              "nutriments": {"energy_kcal": None, "fat": None, "sugars": None, "salt": None, "fiber": None},
              "labels": ["organic"], "packaging": None}),
    ("0004", {"name": "Greek yogurt 32 oz", "brand": "Harbor Farms", "category": "dairy",
              "nutriments": {"energy_kcal": 100, "fat": 0, "sugars": 6, "salt": 0.1, "fiber": 0},
              "labels": ["high-protein", "gluten-free"], "packaging": {"material": "pp", "recyclable": True}}),
    ("0005", {"name": "Oat milk 64 oz", "brand": "Green Valley", "category": "dairy-alternatives",
              "nutriments": {"energy_kcal": 120, "fat": 5, "sugars": 7, "salt": None, "fiber": 2},
              "labels": ["vegan", None, "gluten-free", None], "packaging": {"material": "carton", "recyclable": None}}),
    ("0006", {"name": "Baby spinach 5 oz", "brand": "Sunny Fields", "category": "produce",
              "nutriments": {"energy_kcal": 20, "fat": None, "sugars": None, "salt": None, "fiber": 2},
              "labels": [], "packaging": {"material": "pet", "recyclable": False}}),
    ("0007", {"name": "Salted butter 1 lb", "brand": None, "category": None,
              "nutriments": {"energy_kcal": 100, "fat": 11, "sugars": 0, "salt": 0.2, "fiber": None},
              "labels": None, "packaging": {"material": "paper", "recyclable": None}}),
    ("0008", {"name": "Bagels 6 ct", "brand": "Harbor Bakery", "category": "bakery",
              "nutriments": {"energy_kcal": 280, "fat": 2, "sugars": 6, "salt": 1.2, "fiber": 3},
              "labels": [None], "packaging": {"material": "ldpe", "recyclable": False}}),
]
NUTRIMENTS = 5


# ---------- pure-Python reference ----------
def strip(v, include_arrays: bool = True):
    """What variant_strip_nulls does: drop object fields whose value is null (recursively); also drop null array
    elements unless include_arrays is False. A top-level null stays null."""
    if isinstance(v, dict):
        return {k: strip(x, include_arrays) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [strip(x, include_arrays) for x in v if x is not None or not include_arrays]
    return v


def compact(v) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def nulls(v) -> int:
    if v is None:
        return 1
    if isinstance(v, dict):
        return sum(nulls(x) for x in v.values())
    if isinstance(v, list):
        return sum(nulls(x) for x in v)
    return 0


def per_product_reference() -> list[dict]:
    out = []
    for code, p in PRODUCTS:
        s, k = strip(p), strip(p, False)
        out.append({"code": code, "bytes_raw": len(compact(p)), "bytes_stripped": len(compact(s)),
                    "nutriments_filled": len(s.get("nutriments") or {}), "labels": len(s.get("labels") or []),
                    "label_slots_kept": len(k.get("labels") or []), "packaging_empty": s.get("packaging") == {},
                    "has_packaging": "packaging" in s})
    return out


def summarise(per: list[dict], checks: dict) -> dict:
    raw, slim = sum(r["bytes_raw"] for r in per), sum(r["bytes_stripped"] for r in per)
    filled = sum(r["nutriments_filled"] for r in per)
    return {
        "products": len(per), "null_values_removed": checks["null_values"], "bytes_raw": raw, "bytes_stripped": slim,
        "bytes_saved_pct": round(100 * (raw - slim) / raw, 1),
        "nutriment_completeness_pct": round(100 * filled / (NUTRIMENTS * len(per)), 1),
        "fully_described": sorted(r["code"] for r in per if r["nutriments_filled"] == NUTRIMENTS),
        "no_nutriments": sorted(r["code"] for r in per if r["nutriments_filled"] == 0),
        "per_product": per, "checks": checks,
    }


EXPECTED_CHECKS = {"null_values": sum(nulls(p) for _, p in PRODUCTS), "stripped_equal_reference": True,
                   "kept_arrays_equal_reference": True,
                   "spot": {"top_level_null": "null", "sql_null_is_null": True, "nested": '{"a":{"c":[1]}}',
                            "keep_arrays": '{"a":[1,null]}'}}


def reference() -> dict:
    return summarise(per_product_reference(), json.loads(json.dumps(EXPECTED_CHECKS)))


# ---------- Spark (local pyspark 4.3, or a Databricks serverless job) ----------
def _lit(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


SPOT_SQL = """
SELECT to_json(variant_strip_nulls(parse_json('null')))                                   AS top_level_null,
       variant_strip_nulls(CAST(NULL AS VARIANT)) IS NULL                                  AS sql_null_is_null,
       to_json(variant_strip_nulls(parse_json('{"a": {"b": null, "c": [1, null]}}')))      AS nested,
       to_json(variant_strip_nulls(parse_json('{"a": [1, null], "b": null}'), false))      AS keep_arrays
"""
REPORT_SQL = """
SELECT code,
  length(to_json(payload))                                                         AS bytes_raw,
  length(to_json(stripped))                                                        AS bytes_stripped,
  coalesce(size(map_keys(CAST(variant_get(stripped, '$.nutriments') AS MAP<STRING, VARIANT>))), 0) AS nutriments_filled,
  coalesce(array_size(CAST(variant_get(stripped, '$.labels') AS ARRAY<VARIANT>)), 0) AS labels,
  coalesce(array_size(CAST(variant_get(kept, '$.labels') AS ARRAY<VARIANT>)), 0)     AS label_slots_kept,
  coalesce(to_json(variant_get(stripped, '$.packaging')) = '{{}}', false)           AS packaging_empty,
  variant_get(stripped, '$.packaging') IS NOT NULL                                 AS has_packaging,
  to_json(stripped) AS stripped_json, to_json(kept) AS kept_json
FROM (SELECT code, payload, variant_strip_nulls(payload) AS stripped, variant_strip_nulls(payload, false) AS kept
      FROM {src})
ORDER BY code
"""


def run_spark(spark) -> dict:
    rows = ", ".join(f"('{c}', {_lit(json.dumps(p))})" for c, p in PRODUCTS)
    spark.sql(f"SELECT code, parse_json(j) AS payload FROM VALUES {rows} AS t(code, j)") \
        .createOrReplaceTempView("product_payloads")
    per, same_s, same_k = [], True, True
    want = dict(PRODUCTS)
    for r in spark.sql(REPORT_SQL.format(src="product_payloads")).collect():
        same_s &= json.loads(r.stripped_json) == strip(want[r.code])
        same_k &= json.loads(r.kept_json) == strip(want[r.code], False)
        per.append({"code": r.code, "bytes_raw": int(r.bytes_raw), "bytes_stripped": int(r.bytes_stripped),
                    "nutriments_filled": int(r.nutriments_filled), "labels": int(r.labels),
                    "label_slots_kept": int(r.label_slots_kept), "packaging_empty": bool(r.packaging_empty),
                    "has_packaging": bool(r.has_packaging)})
    spot = spark.sql(SPOT_SQL).collect()[0].asDict()
    nulls_spark = sum(nulls(json.loads(r.j)) for r in spark.sql(
        "SELECT to_json(payload) AS j FROM product_payloads").collect())
    checks = {"null_values": nulls_spark, "stripped_equal_reference": bool(same_s),
              "kept_arrays_equal_reference": bool(same_k), "spot": spot}
    return summarise(per, checks)


def run_cloud_job() -> dict:
    import pyspark
    from pyspark.sql import SparkSession
    out = run_spark(SparkSession.builder.getOrCreate())
    out["engine"] = f"variant_strip_nulls on a Databricks serverless job (pyspark {pyspark.__version__})"
    return out


def main(argv: list[str]) -> int:
    if argv:  # serverless job: argv[0] is a Unity Catalog volume folder
        text = json.dumps(run_cloud_job(), sort_keys=True)
        print("DAY41_RESULT " + text)    # read back from the task's logs if the output file can't be read
        Path(argv[0].rstrip("/")).mkdir(parents=True, exist_ok=True)
        Path(argv[0].rstrip("/"), "result.json").write_text(text)
        return 0
    out = {"engine": "pure-Python reference (local) / variant_strip_nulls in Spark SQL on a Databricks serverless job "
                     "(cloud)", **reference()}
    (Path(__file__).resolve().parent / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("products", "null_values_removed", "bytes_raw", "bytes_stripped",
                                          "bytes_saved_pct", "nutriment_completeness_pct")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
