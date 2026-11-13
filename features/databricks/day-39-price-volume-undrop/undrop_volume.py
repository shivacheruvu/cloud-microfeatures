"""Day 39 (Databricks): get a store price-file volume back after someone drops it, with **UNDROP VOLUME** (Unity
Catalog, 2026-11-07): `SHOW VOLUMES DROPPED` finds the dropped volume's ID and `UNDROP VOLUME WITH ID '<id>'` brings
back the volume, its files (managed volumes), its comment and its grants.

  python undrop_volume.py                                   # local reference: file checksums and price totals,
                                                            # writes result.json
  python undrop_volume.py CATALOG SCHEMA VOLUME ID OUTDIR   # serverless job: snapshot, DROP, SHOW VOLUMES DROPPED,
                                                            # UNDROP, snapshot again; writes OUTDIR/result.json

The job's snapshot of the volume (files + SHA-256, rows and price total per file read with Spark, comment, grants)
is taken before the drop and after the undrop; run_cloud.py requires both to equal the local reference.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
COMMENT = "cloud-microfeatures day 39: store shelf price files"
GRANTEE, PRIVILEGE = "account users", "READ VOLUME"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def price_files() -> list[Path]:
    return sorted(DATA.glob("prices_*.csv"))


def file_summary(name: str, raw: bytes) -> dict:
    rows = list(csv.DictReader(raw.decode().splitlines()))
    return {"file": name, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(rows),
            "price_total": str(sum(Decimal(r["price"]) for r in rows))}


def reference() -> dict:
    files = [file_summary(p.name, p.read_bytes()) for p in price_files()]
    return {"files": files, "file_count": len(files), "rows": sum(f["rows"] for f in files),
            "price_total": str(sum(Decimal(f["price_total"]) for f in files)), "comment": COMMENT,
            "grants": [[GRANTEE, PRIVILEGE]]}


def undrop_sql(volume_id: str) -> str:
    """UNDROP VOLUME only takes an ID (there is no by-name form); the ID goes into a literal, so it must be a UUID."""
    if not UUID.match(volume_id or ""):
        raise ValueError("volume_id is not a UUID")
    return f"UNDROP VOLUME WITH ID '{volume_id}'"


def fqn(catalog: str, schema: str, volume: str) -> str:
    for part in (catalog, schema, volume):
        if not NAME.match(part):
            raise ValueError(f"bad identifier {part!r}")
    return f"`{catalog}`.`{schema}`.`{volume}`"


def pick_dropped(rows: list[dict], volume: str, expected_id: str) -> dict:
    """The SHOW VOLUMES DROPPED row to undrop. Earlier drops of a volume with the same name (yesterday's run, a
    re-created volume) are listed too, so match on the ID recorded when the volume was created, not on the name."""
    same_name = [r for r in rows if r.get("volume_name") == volume]
    mine = [r for r in same_name if r.get("volume_id") == expected_id]
    if not mine:
        raise LookupError(f"volume {volume} with the expected ID is not in SHOW VOLUMES DROPPED "
                          f"({len(same_name)} dropped volume(s) with that name)")
    return {**mine[0], "same_name_dropped": len(same_name)}


def describe_comment(rows: list[dict]) -> str | None:
    """DESCRIBE VOLUME's comment, whether it comes back as one wide row or as name/value rows."""
    for r in rows:
        low = {str(k).lower(): v for k, v in r.items()}
        if "comment" in low:
            return low["comment"]
        vals = list(r.values())
        if len(vals) >= 2 and str(vals[0]).strip().lower() == "comment":
            return vals[1]
    return None


def snapshot(spark, catalog: str, schema: str, volume: str) -> dict:
    """What the store team relies on: the files byte for byte, what Spark reads from them, the comment, the grant."""
    root = f"/Volumes/{catalog}/{schema}/{volume}"
    files = []
    for name in sorted(n for n in os.listdir(root) if n.endswith(".csv")):
        s = file_summary(name, Path(root, name).read_bytes())
        df = spark.read.option("header", True).csv(f"{root}/{name}")
        row = df.selectExpr("count(*) AS n", "CAST(sum(CAST(price AS DECIMAL(10,2))) AS STRING) AS t").first()
        s["spark_rows"], s["spark_price_total"] = int(row.n), row.t
        files.append(s)
    comment = describe_comment([r.asDict() for r in
                                spark.sql(f"DESCRIBE VOLUME {fqn(catalog, schema, volume)}").collect()])
    grants = sorted({(r["principal"], r["action_type"]) for r in
                     (g.asDict() for g in spark.sql(f"SHOW GRANTS ON VOLUME {fqn(catalog, schema, volume)}").collect())
                     if r.get("principal") == GRANTEE})
    return {"files": files, "comment": comment, "grants": [list(g) for g in grants]}


def job(catalog: str, schema: str, volume: str, expected_id: str, outdir: str) -> dict:
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    res: dict = {"engine": f"Databricks serverless (Spark {spark.version})"}
    res["before"] = snapshot(spark, catalog, schema, volume)
    spark.sql(f"DROP VOLUME {fqn(catalog, schema, volume)}")
    try:
        os.listdir(f"/Volumes/{catalog}/{schema}/{volume}")
        res["readable_after_drop"] = True
    except OSError:
        res["readable_after_drop"] = False
    dropped = [r.asDict() for r in spark.sql(f"SHOW VOLUMES DROPPED IN `{catalog}`.`{schema}`").collect()]
    row = pick_dropped(dropped, volume, expected_id)
    res["same_name_dropped"] = row["same_name_dropped"]
    spark.sql(undrop_sql(row["volume_id"]))
    res["undropped"] = True
    res["after"] = snapshot(spark, catalog, schema, volume)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(res))
    print("DAY39_RESULT " + json.dumps(res))
    return res


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        job(*argv[1:6])
        return 0
    res = {"engine": "plain Python (local reference)", **reference()}
    (HERE / "result.json").write_text(json.dumps(res, indent=2) + "\n")
    print(f"{res['file_count']} price files, {res['rows']} rows, prices total {res['price_total']}; the undropped "
          f"volume must give back the same bytes, comment and grant ({GRANTEE}: {PRIVILEGE})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
