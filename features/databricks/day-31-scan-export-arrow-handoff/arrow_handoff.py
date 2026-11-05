"""Day 31 (Databricks): hand an Arrow-backed pandas scan export straight to Spark with `spark.createDataFrame(pdf)`,
where every column is a PyArrow `ChunkedArray` (new on serverless 2026-08-17: createDataFrame handles pandas
DataFrames with ChunkedArray columns instead of raising type errors).

  python arrow_handoff.py [OUT.json]    # local: pure-Python reference, writes result.json (or OUT.json)

The scan export arrives the way register lanes write it: one Arrow record batch per lane, kept as a Parquet file with
one row group per lane. Reading it back with `types_mapper=pd.ArrowDtype` gives a pandas DataFrame whose columns are
`string[pyarrow]`, `int64[pyarrow]`, `double[pyarrow]`, each a 3-chunk ChunkedArray. That DataFrame goes into Spark
as is (no `.astype`, no `to_numpy`), and the lane report is computed in Spark SQL.

The point to check: price-lookup misses (NULL unit price) and anonymous shoppers (NULL loyalty id) must arrive in
Spark as NULL, so "unpriced scans" is a real count and revenue skips them. Locally result.json comes from a pure-Python
reference; the test also runs the real createDataFrame on local pyspark and compares.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

LANES = ["lane-1", "lane-2", "self-checkout"]
SCANS_PER_LANE = {"lane-1": 14, "lane-2": 11, "self-checkout": 17}
CATALOG = [  # sku, product, shelf price (None: price-lookup miss at the register)
    ("0001", "bananas 1 lb", 0.69), ("0002", "whole milk 1 gal", 3.89), ("0003", "sourdough loaf", 4.99),
    ("0004", "eggs dozen", 3.29), ("0005", "baby spinach 5 oz", 3.49), ("0006", "cheddar 8 oz", 4.29),
    ("0007", "seasonal squash", None), ("0008", "ground coffee 12 oz", 8.99), ("0009", "bulk almonds", None),
    ("0010", "oat milk 64 oz", 4.59), ("0011", "greek yogurt 32 oz", 5.79),
]
SCHEMA = "lane:string,sku:string,product:string,qty:bigint,unit_price:double,loyalty_id:string"


def rows() -> list[dict]:
    """42 scans across 3 lanes. Deterministic, so every run gives the same numbers."""
    out = []
    for li, lane in enumerate(LANES):
        for i in range(SCANS_PER_LANE[lane]):
            sku, product, price = CATALOG[(i * 5 + li * 3) % len(CATALOG)]
            out.append({"lane": lane, "sku": sku, "product": product, "qty": 1 + (i * 3 + li) % 4,
                        "unit_price": price,
                        "loyalty_id": None if (i + li) % 3 == 0 else f"L{100 + (i * 7 + li) % 9}"})
    return out


def export_parquet(data: list[dict]) -> bytes:
    """The register export: one record batch (one Parquet row group) per lane."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = pa.schema([("lane", pa.string()), ("sku", pa.string()), ("product", pa.string()),
                        ("qty", pa.int64()), ("unit_price", pa.float64()), ("loyalty_id", pa.string())])
    batches = [pa.RecordBatch.from_pylist([r for r in data if r["lane"] == lane], schema=schema) for lane in LANES]
    buf = io.BytesIO()
    with pq.ParquetWriter(buf, schema) as w:
        for b in batches:
            w.write_batch(b)
    return buf.getvalue()


def arrow_backed_pandas(parquet: bytes):
    """Read the export back as pandas with Arrow dtypes: every column is a ChunkedArray (one chunk per lane)."""
    import pandas as pd
    import pyarrow.parquet as pq
    return pq.read_table(io.BytesIO(parquet)).to_pandas(types_mapper=pd.ArrowDtype)


def chunk_info(pdf) -> dict:
    import pyarrow as pa
    info = {}
    for c in pdf.columns:
        arr = pdf[c].array._pa_array   # pandas ArrowExtensionArray keeps the ChunkedArray here
        assert isinstance(arr, pa.ChunkedArray)
        info[c] = {"dtype": str(pdf[c].dtype), "chunks": arr.num_chunks}
    return info


LANE_SQL = """
SELECT lane,
       COUNT(*)                                                   AS scans,
       SUM(qty)                                                   AS units,
       ROUND(SUM(qty * unit_price), 2)                            AS revenue,
       COUNT_IF(unit_price IS NULL)                               AS unpriced_scans,
       COUNT_IF(loyalty_id IS NOT NULL)                           AS loyalty_scans,
       COUNT(DISTINCT sku)                                        AS distinct_skus
FROM scans GROUP BY lane ORDER BY lane
"""


def lanes_reference(data: list[dict]) -> list[dict]:
    out = []
    for lane in sorted(LANES):
        r = [x for x in data if x["lane"] == lane]
        out.append({"lane": lane, "scans": len(r), "units": sum(x["qty"] for x in r),
                    "revenue": round(sum(x["qty"] * x["unit_price"] for x in r if x["unit_price"] is not None), 2),
                    "unpriced_scans": sum(x["unit_price"] is None for x in r),
                    "loyalty_scans": sum(x["loyalty_id"] is not None for x in r),
                    "distinct_skus": len({x["sku"] for x in r})})
    return out


def lanes_spark(spark, pdf) -> tuple[str, list[dict]]:
    """The new path: createDataFrame straight from the Arrow-backed pandas DataFrame."""
    df = spark.createDataFrame(pdf)
    df.createOrReplaceTempView("scans")
    res = [r.asDict() for r in spark.sql(LANE_SQL).collect()]
    for r in res:
        r["revenue"] = round(float(r["revenue"]), 2)
    return df.schema.simpleString().removeprefix("struct<").removesuffix(">"), res


def summarise(lanes: list[dict], schema: str, chunks: dict) -> dict:
    unpriced = {p for _, p, price in CATALOG if price is None}
    return {"rows": sum(x["scans"] for x in lanes), "spark_schema": schema, "pandas_columns": chunks,
            "lanes": lanes,
            "revenue": round(sum(x["revenue"] for x in lanes), 2),
            "unpriced_scans": sum(x["unpriced_scans"] for x in lanes),
            "unpriced_products": sorted(unpriced),
            "loyalty_share": round(sum(x["loyalty_scans"] for x in lanes) / sum(x["scans"] for x in lanes), 4)}


def run_cloud_job() -> dict:
    """Serverless job entry: the real createDataFrame on ChunkedArray columns, plus where it ran."""
    import pandas as pd
    import pyarrow as pa
    import pyspark
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    pdf = arrow_backed_pandas(export_parquet(rows()))
    schema, lanes = lanes_spark(spark, pdf)
    out = summarise(lanes, schema, chunk_info(pdf))
    out["engine"] = (f"spark.createDataFrame on Arrow-backed pandas (pyspark {pyspark.__version__}, "
                     f"pandas {pd.__version__}, pyarrow {pa.__version__})")
    return out


def main(argv: list[str]) -> int:
    if argv:  # serverless job: argv[0] is a Unity Catalog volume path for the result
        text = json.dumps(run_cloud_job(), sort_keys=True)
        print("DAY31_RESULT " + text)    # read back from the task's logs if the output file can't be read
        Path(argv[0]).write_text(text)
        return 0
    data = rows()
    chunks = chunk_info(arrow_backed_pandas(export_parquet(data)))
    out = {"engine": "pure-Python reference (local) / spark.createDataFrame on Arrow-backed pandas on a Databricks "
                     "serverless job (cloud)", **summarise(lanes_reference(data), SCHEMA, chunks)}
    (Path(__file__).resolve().parent / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("rows", "revenue", "unpriced_scans", "loyalty_share")}),
          [(x["lane"], x["scans"], x["revenue"], x["unpriced_scans"]) for x in out["lanes"]])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
