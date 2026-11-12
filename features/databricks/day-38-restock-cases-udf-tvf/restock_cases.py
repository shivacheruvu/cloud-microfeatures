"""Day 38 (Databricks): build the night crew's case pick list by passing a scalar SQL UDF straight into the `range`
table-valued function: **scalar UDFs as table-valued function arguments** (Databricks SQL 2026.32, Preview 2026-08-11,
current 2026-08-24).

  python restock_cases.py [WORKDIR]   # no argument: local plain-Python reference, writes result.json
                                      # WORKDIR (a Unity Catalog volume folder): serverless job, runs the SQL with
                                      # spark.sql and writes WORKDIR/result.json

`cases_to_pick(on_hand, par, pack)` is a temporary SQL UDF: how many cases to bring from the back room to get a shelf
back to par. The pick list is one row per case:

  SELECT 'MLK-2L' AS sku, id AS case_no FROM range(1, cases_to_pick(7, 30, 6) + 1)

Open-source Spark (4.3) refuses that with NON_FOLDABLE_ARGUMENT: TVF arguments must be constant-foldable and a UDF
call isn't. Databricks now accepts it. The same list is also built the old way (`explode(sequence(...))` in the SELECT
list, which runs on any Spark) and both must equal the reference.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Night count for store chi-01, aisle by aisle: sku, name, aisle, on_hand (units on shelf), par (units), pack (units/case)
SHELF = [
    ("MLK-2L", "Whole milk 2 L", "A01", 7, 30, 6),
    ("YOG-500", "Greek yogurt 500 g", "A01", 11, 24, 12),
    ("EGG-12", "Free-range eggs x12", "A02", 18, 18, 6),      # at par: 0 cases, range(1, 1) is empty
    ("PAS-500", "Penne 500 g", "B07", 3, 40, 20),
    ("RIC-2KG", "Basmati rice 2 kg", "B07", 9, 12, 8),
    ("OIL-1L", "Olive oil 1 L", "B08", 25, 20, 6),            # over par (overstock): GREATEST keeps it at 0
    ("CRL-500", "Oat cereal 500 g", "B09", 0, 36, 12),        # empty shelf
    ("PEA-1KG", "Frozen peas 1 kg", "F03", 5, 30, 10),
]

UDF_SQL = """
CREATE OR REPLACE TEMPORARY FUNCTION cases_to_pick(on_hand INT, par INT, pack INT)
RETURNS INT
RETURN CAST(GREATEST(0, CEIL((par - on_hand) / pack)) AS INT)
"""


def _lit(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def tvf_sql() -> str:
    """New way: the scalar UDF is an argument of the range TVF, one TVF call per shelf line."""
    parts = [f"SELECT {_lit(sku)} AS sku, {_lit(aisle)} AS aisle, id AS case_no\n"
             f"FROM range(1, cases_to_pick({on}, {par}, {pack}) + 1)" for sku, _, aisle, on, par, pack in SHELF]
    return "\nUNION ALL\n".join(parts) + "\nORDER BY aisle, sku, case_no"


def explode_sql() -> str:
    """Old way (works on any Spark): the UDF in the SELECT list, rows made with explode(sequence(...))."""
    values = ",\n  ".join(f"({_lit(sku)}, {_lit(aisle)}, {on}, {par}, {pack})" for sku, _, aisle, on, par, pack in SHELF)
    return ("SELECT sku, aisle, case_no FROM (\n"
            "  SELECT sku, aisle, cases_to_pick(on_hand, par, pack) AS n FROM VALUES\n"
            f"  {values}\n  AS shelf(sku, aisle, on_hand, par, pack)\n) "
            "LATERAL VIEW explode(sequence(1, n)) AS case_no WHERE n > 0\nORDER BY aisle, sku, case_no")


def cases_to_pick(on_hand: int, par: int, pack: int) -> int:
    return max(0, math.ceil((par - on_hand) / pack))


def reference_rows() -> list[dict]:
    rows = [{"sku": sku, "aisle": aisle, "case_no": n}
            for sku, _, aisle, on, par, pack in SHELF for n in range(1, cases_to_pick(on, par, pack) + 1)]
    return sorted(rows, key=lambda r: (r["aisle"], r["sku"], r["case_no"]))


def summarize(rows: list[dict]) -> dict:
    by_sku: dict[str, int] = {}
    for r in rows:
        by_sku[r["sku"]] = max(by_sku.get(r["sku"], 0), int(r["case_no"]))
    pack = {s[0]: s[5] for s in SHELF}
    by_aisle: dict[str, int] = {}
    for r in rows:
        by_aisle[r["aisle"]] = by_aisle.get(r["aisle"], 0) + 1
    return {
        "shelf_lines": len(SHELF),
        "cases": len(rows),
        "units": sum(pack[s] * n for s, n in by_sku.items()),
        "cases_by_sku": {s[0]: by_sku.get(s[0], 0) for s in SHELF},
        "cases_by_aisle": dict(sorted(by_aisle.items())),
        "lines_needing_no_cases": sorted(s[0] for s in SHELF if s[0] not in by_sku),
        "pick_list": [f"{r['aisle']} {r['sku']} #{r['case_no']}" for r in rows],
    }


def spark_rows(spark, sql: str) -> list[dict]:
    return [{"sku": r.sku, "aisle": r.aisle, "case_no": int(r.case_no)} for r in spark.sql(sql).collect()]


def spark_result(spark) -> dict:
    spark.sql(UDF_SQL)
    explode_rows = spark_rows(spark, explode_sql())
    try:
        tvf_rows, tvf_error = spark_rows(spark, tvf_sql()), None
    except Exception as e:  # noqa: BLE001 - reported, the caller decides
        tvf_rows, tvf_error = None, str(e).splitlines()[0][:300]
    return {"udf_in_tvf": summarize(tvf_rows) if tvf_rows is not None else None, "udf_in_tvf_error": tvf_error,
            "explode_sequence": summarize(explode_rows)}


def main(argv: list[str]) -> int:
    if len(argv) > 1:  # serverless job
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        res = {"engine": f"Databricks serverless (Spark {spark.version})", **spark_result(spark)}
        out = Path(argv[1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(json.dumps(res))
        print("DAY38_RESULT " + json.dumps(res))
        return 0
    res = {"engine": "plain Python (local reference)", **summarize(reference_rows())}
    (HERE / "result.json").write_text(json.dumps(res, indent=2) + "\n")
    print(f"{res['cases']} cases ({res['units']} units) for {res['shelf_lines']} shelf lines; "
          f"by aisle {res['cases_by_aisle']}; no cases: {res['lines_needing_no_cases']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
