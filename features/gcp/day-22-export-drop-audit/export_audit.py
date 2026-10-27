"""Day 22 (Google Cloud): audit the store export drop folder with BigQuery's OBJ.LIST.

  python export_audit.py                 # local: list data/exports/, check it against the store x day calendar;
                                         # writes result.json
  python export_audit.py --sql BUCKET    # print the BigQuery SQL for a bucket holding exports/<store>_<date>.csv
  python export_audit.py --compare OUT   # check bq's JSON output against the local result (cloud.sh)

Each store's point-of-sale system drops one CSV a day into exports/. The audit marks every expected (store, day)
as ok, empty (a zero-byte file: the export job ran but wrote nothing) or missing. In the cloud, OBJ.LIST reads the
file listing and sizes straight from Cloud Storage; locally the same rules run over the folder in this repo.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORTS = HERE / "data" / "exports"
STORES = ("east", "north", "south")  # also in audit.sql
FIRST, LAST = date(2026, 10, 23), date(2026, 10, 25)  # also in audit.sql
NAME = re.compile(r"^([a-z]+)_(\d{4}-\d{2}-\d{2})\.csv$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9_-]{1,61}[a-z0-9]$")


def listing(folder: Path = EXPORTS) -> list[dict]:
    """What OBJ.LIST returns for exports/*, reduced to the parts the audit reads: name and size."""
    return [{"name": p.name, "size": p.stat().st_size} for p in sorted(folder.iterdir()) if p.is_file()]


def audit(files: list[dict]) -> list[dict]:
    seen = {}
    for f in files:
        m = NAME.match(f["name"])
        if m:
            seen[(m.group(1), m.group(2))] = f["size"]
    out, d = [], FIRST
    days = []
    while d <= LAST:
        days.append(d.isoformat())
        d += timedelta(days=1)
    for s in STORES:
        for day in days:
            size = seen.get((s, day))
            status = "missing" if size is None else "empty" if size == 0 else "ok"
            out.append({"store": s, "export_date": day, "status": status, "bytes": size or 0})
    return out


def sql(bucket: str) -> str:
    if not BUCKET.match(bucket):
        raise ValueError("bad bucket name")
    return (HERE / "audit.sql").read_text().replace("{bucket}", bucket)


def compare(cloud: list[dict], local: list[dict]) -> list[str]:
    errors = []
    try:
        got = [{"store": c["store"], "export_date": c["export_date"], "status": c["status"],
                "bytes": int(c["bytes"])} for c in cloud]  # bq's JSON gives INT64 as strings
    except (KeyError, TypeError, ValueError) as e:
        return [f"unreadable BigQuery output: {e!r}"]
    if len(got) != len(local):
        errors.append(f"BigQuery returned {len(got)} rows, local {len(local)}")
    for g, w in zip(got, local):
        if g != w:
            errors.append(f"{w['store']}/{w['export_date']}: BigQuery {g} vs local {w}")
    return errors


def summarise(rows: list[dict], files: list[dict]) -> dict:
    count = {k: sum(r["status"] == k for r in rows) for k in ("ok", "empty", "missing")}
    return {
        "files_listed": len(files),
        "bytes_listed": sum(f["size"] for f in files),
        "expected_exports": len(rows),
        **count,
        "problems": [f"{r['store']} {r['export_date']}: {r['status']}" for r in rows if r["status"] != "ok"],
        "complete_stores": [s for s in STORES if all(r["status"] == "ok" for r in rows if r["store"] == s)],
    }


if __name__ == "__main__":
    files = listing()
    local = audit(files)
    if len(sys.argv) > 2 and sys.argv[1] == "--sql":
        print(sql(sys.argv[2]))
    elif len(sys.argv) > 2 and sys.argv[1] == "--compare":
        raw = Path(sys.argv[2]).read_text()
        start = raw.find("[")  # bq may print a warning line before the JSON
        cloud = json.loads(raw[start:]) if start >= 0 else []
        errors = compare(cloud, local)
        for e in errors[:10]:
            print(f"::error title=Day 22 OBJ.LIST audit differs from local::{e}")
        if errors:
            raise SystemExit(1)
        print("::notice title=Google Cloud day 22::OBJ.LIST inventoried the drop bucket in one BigQuery query and "
              f"matched local: {json.dumps(summarise(local, files))[:700]}")
    else:
        summary = summarise(local, files)
        (HERE / "result.json").write_text(json.dumps({"engine": "Python over data/exports (local) / BigQuery "
                                                      "OBJ.LIST over a Cloud Storage bucket (cloud)", **summary},
                                                     indent=2) + "\n")
        print(json.dumps({**summary, "rows": local}, indent=2))
