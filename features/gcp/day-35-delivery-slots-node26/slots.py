"""Day 35 (Google Cloud): delivery promises across the end of daylight saving time, run on a Cloud Run job with
Google's **Node.js 26 runtime** base image (nodejs26, Preview, Cloud Run / Cloud Run functions release notes
2026-07-27).

  python slots.py            # Python reference (zoneinfo, math.fsum, re.escape), writes result.json
  python slots.py --cloud    # GitHub Actions: run job26.js on the nodejs26 runtime image, compare, delete the job

`job26.js` is the same computation in JavaScript for Node.js 26 (RegExp.escape, Uint8Array.fromBase64; Temporal behind
--harmony-temporal when available, else Intl.DateTimeFormat). The cloud result must equal this reference, and the job
must report Node 26.
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
DATA = HERE / "data.json"
JOB = "cmf-d35-node26-slots"
RUNTIME_ID = "nodejs26"
# nodejs26 is published for the google-24 (default) and google-24-full stacks; the second is the fallback.
IMAGES = tuple(f"us-central1-docker.pkg.dev/serverless-runtimes/{stack}/runtimes/{RUNTIME_ID}"
               for stack in ("google-24", "google-24-full"))
KEYS = ("orders", "stores", "late_if_naive", "deliveries_in_repeated_hour", "total_naive", "total_precise",
        "name_matches", "utf8_names")


def load(path: Path = DATA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def local(s: str, tz: str) -> datetime:
    """A local wall-clock time; an ambiguous one (01:xx on the fall-back night) takes the earlier offset, which is
    what Temporal's default disambiguation ('compatible') does. fold=0 is the earlier one."""
    return datetime.fromisoformat(s).replace(tzinfo=ZoneInfo(tz), fold=0)


def stamp(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def plus_hours(dt: datetime, hours: int) -> datetime:
    """Exact elapsed hours (Python's aware + timedelta is wall-clock arithmetic, so go through UTC)."""
    return (dt.astimezone(timezone.utc) + timedelta(hours=hours)).astimezone(dt.tzinfo)


def hours_between(a: datetime, b: datetime) -> float:
    return (b.astimezone(timezone.utc) - a.astimezone(timezone.utc)).total_seconds() / 3600


def hours_in_day(day: str, tz: str) -> float:
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, tzinfo=ZoneInfo(tz))
    nxt = d + timedelta(days=1)
    return hours_between(start, datetime(nxt.year, nxt.month, nxt.day, tzinfo=ZoneInfo(tz)))


def naive_sum(xs) -> float:
    total = 0.0
    for x in xs:          # plain left fold, like [].reduce((a, b) => a + b, 0); Python's sum() is compensated
        total += x
    return total


def reference(data: dict | None = None) -> dict:
    data = data or load()
    orders, late, repeated = [], 0, 0
    for o in data["orders"]:
        tz = data["stores"][o["store"]]
        placed = local(o["placed"], tz)
        due = plus_hours(placed, o["promise_hours"])
        naive_due = datetime.fromisoformat(o["placed"]) + timedelta(hours=o["promise_hours"])
        pickup = local((datetime.fromisoformat(o["placed"]) + timedelta(days=1)).isoformat(), tz)
        is_late = naive_due.isoformat(timespec="minutes") != due.replace(tzinfo=None).isoformat(timespec="minutes")
        late += is_late
        # the wall-clock hour 01:00-01:59 that happens twice; count deliveries promised in its second pass
        second_pass = due.utcoffset() != local(due.replace(tzinfo=None).isoformat(), tz).utcoffset()
        repeated += second_pass
        orders.append({"id": o["id"], "store": o["store"], "placed": stamp(placed), "deliver_by": stamp(due),
                       "naive_deliver_by": naive_due.isoformat(timespec="minutes"), "late_if_naive": is_late,
                       "next_day_pickup": stamp(pickup), "hours_to_pickup": hours_between(placed, pickup)})
    stores = {}
    for sid, tz in sorted(data["stores"].items()):
        shift = data["restock_shift"]
        stores[sid] = {"time_zone": tz, "hours_in_day": hours_in_day(data["day"], tz),
                       "restock_hours": hours_between(local(shift["start"], tz), local(shift["end"], tz))}
    prices = [p for o in data["orders"] for _, p in o["lines"]]
    texts = [n for o in data["orders"] for n, _ in o["lines"]]
    matches = []
    for name in data["catalog_names"]:
        exact = re.compile(re.escape(name), re.IGNORECASE)
        loose = re.compile(name, re.IGNORECASE)
        matches.append([name, sum(1 for t in texts if exact.search(t)), sum(1 for t in texts if loose.search(t))])
    return {
        "orders": orders,
        "stores": stores,
        "late_if_naive": late,
        "deliveries_in_repeated_hour": repeated,
        "total_naive": naive_sum(prices),
        "total_precise": math.fsum(prices),
        "name_matches": matches,
        "utf8_names": [t for t in texts if not t.isascii()],
    }


def write_result() -> dict:
    res = {"engine": "Python zoneinfo reference (local) / job26.js on the Cloud Run nodejs26 runtime image (cloud)",
           "image": IMAGES[0], **reference()}
    (HERE / "result.json").write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return res


if __name__ == "__main__":
    if "--cloud" in sys.argv:
        import cloud_run
        sys.exit(cloud_run.run())
    r = write_result()
    print(json.dumps({k: r[k] for k in KEYS if k != "orders"}, ensure_ascii=False, indent=1))
    for o in r["orders"]:
        print(o)
