"""Day 33 (Databricks): quarantine malformed VARIANT scan payloads with `is_valid_variant` (serverless compute,
Databricks Runtime 19 updates, 2026-08-17) before anything extracts fields from them.

  python scan_payloads.py [WORKDIR [CATALOG]]   # local: pure-Python reference, writes result.json
                                                # serverless job: WORKDIR is a Unity Catalog volume folder

A lane-scanner exporter writes scan events to Parquet with a VARIANT column `payload` ({"lane", "sku", "qty",
"price"}). Three of its 14 rows come out broken the way a buggy writer breaks binary variants:
  E05 truncated   - the value bytes are cut 3 bytes short
  E09 bad_header  - the metadata starts with an unknown version byte
  E12 bad_type    - the value starts with a type byte the variant spec doesn't define

Steps (the same in local pyspark 4.3 and on a Databricks serverless job):
  1. extract fields straight from the raw file          -> the whole query fails with MALFORMED_VARIANT
  2. SELECT event_id, is_valid_variant(payload)          -> 11 true, 3 false
  3. write only the valid rows to a clean Parquet folder -> quarantine list = the false ones
  4. per-lane report from the clean folder               -> must equal the pure-Python reference
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

EVENTS = [  # event_id, lane, sku, qty, price
    ("E01", 1, "0004", 2, "3.29"), ("E02", 1, "0010", 1, "4.59"), ("E03", 2, "0008", 1, "8.99"),
    ("E04", 2, "0001", 6, "0.69"), ("E05", 3, "0002", 1, "3.89"), ("E06", 3, "0011", 2, "5.79"),
    ("E07", 1, "0006", 1, "4.29"), ("E08", 2, "0004", 1, "3.29"), ("E09", 3, "0003", 1, "4.99"),
    ("E10", 1, "0002", 2, "3.89"), ("E11", 3, "0010", 2, "4.59"), ("E12", 2, "0008", 2, "8.99"),
    ("E13", 2, "0003", 1, "4.99"), ("E14", 3, "0001", 3, "0.69"),
]
CORRUPT = {"E05": "truncated", "E09": "bad_header", "E12": "bad_type"}

EXTRACT_SQL = """
SELECT variant_get(payload, '$.lane', 'int')              AS lane,
       SUM(variant_get(payload, '$.qty', 'int'))          AS units,
       SUM(variant_get(payload, '$.qty', 'int') * variant_get(payload, '$.price', 'decimal(10,2)')) AS revenue,
       COUNT(*)                                           AS scans
FROM {src} GROUP BY 1 ORDER BY 1
"""
VALIDITY_SQL = "SELECT event_id, is_valid_variant(payload) AS ok FROM {src} ORDER BY event_id"
CLEAN_SQL = "SELECT event_id, payload FROM {src} WHERE is_valid_variant(payload)"
SPOT_CHECKS_SQL = """
SELECT is_valid_variant(parse_json('{"lane": 1, "sku": "0004"}')) AS parsed_json,
       is_valid_variant(parse_json('null'))                       AS json_null,
       is_valid_variant(CAST(NULL AS VARIANT))                    AS sql_null
"""


def payload_json(lane: int, sku: str, qty: int, price: str) -> str:
    return f'{{"lane": {lane}, "sku": "{sku}", "qty": {qty}, "price": {price}}}'


def corrupt(kind: str, metadata: bytes, value: bytes) -> tuple[bytes, bytes]:
    if kind == "truncated":
        return metadata, value[:-3]
    if kind == "bad_header":
        return b"\x02" + metadata[1:], value          # version 2 isn't defined (only 1)
    if kind == "bad_type":
        return metadata, b"\xff" + value[1:]          # basic type 3 (array) with an undefined header for it
    raise ValueError(kind)


# ---------- pure-Python reference ----------
def report_reference() -> list[dict]:
    out = {}
    for eid, lane, _sku, qty, price in EVENTS:
        if eid in CORRUPT:
            continue
        r = out.setdefault(lane, {"lane": lane, "units": 0, "revenue": Decimal("0"), "scans": 0})
        r["units"] += qty
        r["revenue"] += qty * Decimal(price)
        r["scans"] += 1
    return [{**r, "revenue": float(r["revenue"])} for _, r in sorted(out.items())]


EXPECTED_CHECKS = {"raw_extract": "MALFORMED_VARIANT", "clean_extract_ok": True,
                   "spot": {"parsed_json": True, "json_null": True, "sql_null": None}}


def summarise(by_lane: list[dict], validity: dict, checks: dict) -> dict:
    return {"events": len(validity), "valid": sum(1 for v in validity.values() if v),
            "quarantined": sorted(k for k, v in validity.items() if not v),
            "quarantine_kinds": {k: CORRUPT.get(k, "?") for k, v in sorted(validity.items()) if not v},
            "units": sum(r["units"] for r in by_lane), "revenue": round(sum(r["revenue"] for r in by_lane), 2),
            "by_lane": by_lane, "checks": checks}


def reference() -> dict:
    validity = {eid: eid not in CORRUPT for eid, *_ in EVENTS}
    return summarise(report_reference(), validity, json.loads(json.dumps(EXPECTED_CHECKS)))


# ---------- Spark (local pyspark 4.3, or a Databricks serverless job) ----------
def _err_class(e: Exception) -> str:
    text = str(e)
    if "MALFORMED_VARIANT" in text:
        return "MALFORMED_VARIANT"
    return text.splitlines()[0][:120] if text else type(e).__name__


RAW_SCHEMA = "event_id STRING, payload VARIANT"


def write_raw(spark, workdir: str) -> str:
    """Spark encodes the 14 payloads (parse_json), then the 'buggy exporter' writes them to Parquet with pyarrow as
    plain (metadata, value) binaries, breaking three on the way. Readers apply RAW_SCHEMA to see a VARIANT column."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    rows = ", ".join(f"('{e}', '{payload_json(lane, s, q, p)}')" for e, lane, s, q, p in EVENTS)
    records = []
    for r in spark.sql(f"SELECT event_id, parse_json(j) AS payload FROM VALUES {rows} AS t(event_id, j) "
                       "ORDER BY event_id").collect():
        m, v = bytes(r.payload.metadata), bytes(r.payload.value)
        if r.event_id in CORRUPT:
            m, v = corrupt(CORRUPT[r.event_id], m, v)
        records.append({"event_id": r.event_id, "payload": {"metadata": m, "value": v}})
    schema = pa.schema([("event_id", pa.string()),
                        ("payload", pa.struct([("metadata", pa.binary()), ("value", pa.binary())]))])
    raw = f"{workdir}/raw"
    Path(raw).mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(records, schema=schema), f"{raw}/scans.parquet")
    return raw


def run_spark(spark, workdir: str) -> dict:
    raw = write_raw(spark, workdir)
    spark.read.schema(RAW_SCHEMA).parquet(raw).createOrReplaceTempView("raw_scans")

    def extract(src: str) -> list[dict]:
        return [{"lane": int(r.lane), "units": int(r.units), "revenue": float(r.revenue), "scans": int(r.scans)}
                for r in spark.sql(EXTRACT_SQL.format(src=src)).collect()]

    checks = {}
    try:                                   # 1. one malformed row sinks the whole extract
        extract("raw_scans")
        checks["raw_extract"] = "succeeded"
    except Exception as e:  # noqa: BLE001 - the failure is the expected outcome
        checks["raw_extract"] = _err_class(e)
    validity = {r.event_id: r.ok for r in spark.sql(VALIDITY_SQL.format(src="raw_scans")).collect()}   # 2.
    clean = f"{workdir}/clean"                                                                          # 3.
    spark.sql(CLEAN_SQL.format(src="raw_scans")).write.mode("overwrite").parquet(clean)
    spark.read.parquet(clean).createOrReplaceTempView("clean_scans")
    by_lane = extract("clean_scans")                                                                    # 4.
    checks["clean_extract_ok"] = True
    spot = spark.sql(SPOT_CHECKS_SQL).first()
    checks["spot"] = {"parsed_json": spot.parsed_json, "json_null": spot.json_null, "sql_null": spot.sql_null}
    return summarise(by_lane, validity, checks)


def run_cloud_job(workdir: str) -> dict:
    import pyspark
    from pyspark.sql import SparkSession
    out = run_spark(SparkSession.builder.getOrCreate(), workdir)
    out["engine"] = f"is_valid_variant on a Databricks serverless job (pyspark {pyspark.__version__})"
    return out


def main(argv: list[str]) -> int:
    if argv:  # serverless job: argv[0] is a Unity Catalog volume folder
        text = json.dumps(run_cloud_job(argv[0].rstrip("/")), sort_keys=True)
        print("DAY33_RESULT " + text)    # read back from the task's logs if the output file can't be read
        Path(argv[0].rstrip("/"), "result.json").write_text(text)
        return 0
    out = {"engine": "pure-Python reference (local) / is_valid_variant in Spark SQL on a Databricks serverless job "
                     "(cloud)", **reference()}
    (Path(__file__).resolve().parent / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("events", "valid", "quarantined", "units", "revenue")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
