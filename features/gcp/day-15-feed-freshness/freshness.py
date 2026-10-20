"""Day 15 (Google Cloud): one freshness check for three differently shaped feeds, with an ANY TABLE table-valued
function (BigQuery, GA 2026-08-11). Scans, supplier deliveries and shelf-tag price changes each carry the time the
event happened (event_ts) and the time it landed in the warehouse (loaded_ts), plus their own columns. One TVF with an
ANY TABLE parameter counts, per store, the rows that landed later than the feed's SLA and the worst delay; it is
written once and called on all three tables.

  python freshness.py --make-data    # regenerate data/*.csv (deterministic)
  python freshness.py --local        # plain Python does the same; writes result.json
  python freshness.py --sql [P.DS]   # print the BigQuery multi-statement query (data inlined) for dataset P.DS
  python freshness.py --compare OUT  # check bq's JSON output against the local result

The cloud check (cloud.sh, GitHub Actions):
  * errors: a missing, extra or duplicate (feed, store) row, or any count or delay that differs from local
The tables and the function live in a short-lived dataset that cloud.sh deletes (BigQuery doesn't support temporary
table functions yet). Stores and events are made up.
"""
from __future__ import annotations

import csv
import json
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
STORES = ("S1", "S2", "S3")
# feed -> SLA in minutes (also in freshness.sql)
SLA = {"scans": 15, "deliveries": 120, "price_tags": 30}
FMT = "%Y-%m-%d %H:%M:%S"


def make_data() -> dict[str, list[dict]]:
    rng = random.Random(1015)
    day = datetime(2026, 10, 19, 7, 0, 0)
    out: dict[str, list[dict]] = {"scans": [], "deliveries": [], "price_tags": []}
    # S2's lane uploader stalls over lunch; S3's supplier portal batches overnight; S1 is healthy
    for store in STORES:
        for i in range(30):
            ev = day + timedelta(minutes=rng.randint(0, 14 * 60), seconds=rng.randint(0, 59))
            delay = rng.randint(1, 12)
            if store == "S2" and 4 * 60 <= (ev - day).seconds // 60 <= 6 * 60:  # 11:00-13:00
                delay = rng.randint(20, 95)
            out["scans"].append({"store": store, "lane": f"L{rng.randint(1, 6)}", "sku": f"P{rng.randint(1, 12):02d}",
                                 "qty": str(rng.randint(1, 4)), "event_ts": ev.strftime(FMT),
                                 "loaded_ts": (ev + timedelta(minutes=delay)).strftime(FMT)})
        for i in range(5):
            ev = day + timedelta(minutes=rng.randint(0, 10 * 60))
            delay = rng.randint(10, 90) if store != "S3" else rng.randint(100, 15 * 60)
            out["deliveries"].append({"store": store, "supplier": rng.choice(("Dairyland", "GreenFarm", "BakeCo")),
                                      "cases": str(rng.randint(2, 40)), "event_ts": ev.strftime(FMT),
                                      "loaded_ts": (ev + timedelta(minutes=delay)).strftime(FMT)})
        for i in range(8):
            ev = day + timedelta(minutes=rng.randint(0, 12 * 60))
            delay = rng.randint(2, 40)
            out["price_tags"].append({"store": store, "sku": f"P{rng.randint(1, 12):02d}",
                                      "price_cents": str(rng.randint(69, 599)), "event_ts": ev.strftime(FMT),
                                      "loaded_ts": (ev + timedelta(minutes=delay)).strftime(FMT)})
    for rows in out.values():
        rows.sort(key=lambda r: (r["store"], r["event_ts"]))
    return out


def load() -> dict[str, list[dict]]:
    out = {}
    for name in SLA:
        with open(DATA / f"{name}.csv", newline="") as f:
            out[name] = list(csv.DictReader(f))
    return out


def _struct(r: dict) -> str:
    parts = []
    for k, v in r.items():
        if k.endswith("_ts"):
            parts.append(f"TIMESTAMP '{v}' AS {k}")
        elif v.isdigit():
            parts.append(f"{v} AS {k}")
        else:
            parts.append(f"'{v}' AS {k}")
    return "  STRUCT(" + ", ".join(parts) + ")"


def bigquery_sql(data: dict[str, list[dict]], ds: str = "DATASET") -> str:
    """ds: the short-lived dataset, as project.dataset (cloud.sh passes it; never printed)."""
    sql = "\n".join(l for l in (HERE / "freshness.sql").read_text().splitlines() if not l.lstrip().startswith("--"))
    for name, rows in data.items():
        sql = sql.replace("{" + name + "}", ",\n".join(_struct(r) for r in rows))
    return sql.replace("{ds}", ds)


def _minutes(r: dict) -> int:
    # TIMESTAMP_DIFF(..., MINUTE) counts whole minutes, truncating toward zero
    d = datetime.strptime(r["loaded_ts"], FMT) - datetime.strptime(r["event_ts"], FMT)
    return int(d.total_seconds() // 60)


def run_local(data: dict[str, list[dict]]) -> dict[tuple[str, str], dict]:
    out = {}
    for feed, rows in data.items():
        for store in sorted({r["store"] for r in rows}):
            mins = [_minutes(r) for r in rows if r["store"] == store]
            out[(feed, store)] = {"n_rows": len(mins), "late_rows": sum(m > SLA[feed] for m in mins),
                                  "max_delay_min": max(mins)}
    return out


def read_bq(path: str) -> list[dict]:
    """The rows of the final SELECT. bq may print a warning line, and for a script it can print more than one result
    set, so take the last JSON array whose rows look like ours."""
    text = Path(path).read_text()
    dec, found, i = json.JSONDecoder(), None, text.find("[")
    while i >= 0:
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i = text.find("[", i + 1)
            continue
        if isinstance(obj, list) and (not obj or (isinstance(obj[0], dict) and "late_rows" in obj[0])):
            found = obj
        i = text.find("[", end)
    if found is None:
        raw = text[:500].replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
        print(f"::error title=Unreadable BigQuery output::no result rows found; {raw!r}")
        raise SystemExit(1)
    return found


def compare(cloud: list[dict], local: dict[tuple[str, str], dict]) -> list[str]:
    got: dict[tuple, dict] = {}
    errors = []
    for c in cloud:
        k = (c.get("feed"), c.get("store"))
        if k in got:
            errors.append(f"{'/'.join(map(str, k))}: duplicate row")
        got[k] = c
    errors += [f"{'/'.join(k)}: no output row" for k in local if k not in got]
    errors += [f"{'/'.join(map(str, k))}: unexpected row" for k in got if k not in local]
    for k, want in local.items():
        if k not in got:
            continue
        try:
            have = {f: int(got[k].get(f)) for f in want}  # bq's JSON gives INT64 as strings
        except (TypeError, ValueError):
            errors.append(f"{'/'.join(k)}: unreadable {got[k]}")
            continue
        if have != want:
            errors.append(f"{'/'.join(k)}: BigQuery {have} vs local {want}")
    return errors


def summarise(local: dict[tuple[str, str], dict], data: dict[str, list[dict]]) -> dict:
    breaches = {f"{f}/{s}": [v["late_rows"], v["n_rows"], v["max_delay_min"]]
                for (f, s), v in sorted(local.items()) if v["late_rows"]}
    total = sum(v["n_rows"] for v in local.values())
    late = sum(v["late_rows"] for v in local.values())
    worst = max(local.items(), key=lambda kv: (kv[1]["late_rows"] / kv[1]["n_rows"], kv[0]))
    cols = {f: sorted(data[f][0]) for f in data}
    return {
        "feeds": len(data), "feed_columns": cols, "sla_minutes": SLA, "rows_checked": total, "late_rows": late,
        "breaches": breaches,
        "worst": {"feed": worst[0][0], "store": worst[0][1], "late_rows": worst[1]["late_rows"],
                  "n_rows": worst[1]["n_rows"], "max_delay_min": worst[1]["max_delay_min"]},
        "headline": f"one ANY TABLE function checked {len(data)} feeds with different columns: {late} of {total} rows "
                    f"landed late, in {len(breaches)} feed/store pairs; worst {worst[0][0]} at {worst[0][1]} "
                    f"({worst[1]['late_rows']}/{worst[1]['n_rows']} late, up to {worst[1]['max_delay_min']} min)",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        DATA.mkdir(exist_ok=True)
        for name, rows in make_data().items():
            with open(DATA / f"{name}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        raise SystemExit(0)
    data = load()
    if mode == "--sql":
        print(bigquery_sql(data, *sys.argv[2:3]))
    elif mode == "--compare":
        cloud = read_bq(sys.argv[2])
        local = run_local(data)
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=ANY TABLE TVF differs from local::{len(errors)} difference(s): "
                  f"{json.dumps(errors)[:800]}")
            raise SystemExit(1)
        s = summarise(local, data)
        print(f"::notice title=Google Cloud day 15::the ANY TABLE table function ran on all 3 differently shaped tables and matched "
              f"local for all {len(local)} (feed, store) rows: {s['headline']}; breaches {json.dumps(s['breaches'])}")
    else:
        summary = summarise(run_local(data), data)
        (HERE / "result.json").write_text(json.dumps({"engine": "Python (local) / BigQuery ANY TABLE TVF (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
