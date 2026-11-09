"""Day 35 (Databricks): find temporary price reductions in daily shelf prices and split loyalty scans into shopping
trips with the **MATCH_RECOGNIZE** clause (row pattern recognition, Beta, Databricks SQL release notes 2026-06-30;
Databricks SQL and Databricks Runtime 19+).

  python price_patterns.py [WORKDIR]   # local: reference with Python's re over a per-row symbol string,
                                       # writes result.json
                                       # serverless job: runs the two MATCH_RECOGNIZE queries with spark.sql and
                                       # writes WORKDIR/result.json (WORKDIR is a Unity Catalog volume folder)

Two patterns:
  dips   PATTERN (strt down+ (flat | down)* up+) per SKU over days: a price cut (possibly in steps), held for a
         while, then raised again: a temporary price reduction (TPR). MEASURES give the price before, the lowest
         promo price (the last `down` row), the price it came back to, and the days it was cut and came back.
  trips  PATTERN (strt same_trip*) per loyalty card over scan times: a new trip starts when more than 45 minutes
         pass since the previous scan. MEASURES give MATCH_NUMBER(), first/last scan and scans per trip.

MATCH_RECOGNIZE isn't in open-source Spark, so the local engine classifies each row with the same DEFINE conditions
(D/F/U, or C for "same trip"), joins the symbols into a string per partition and runs the PATTERN as a Python
regular expression: leftmost, greedy with backtracking, and non-overlapping (finditer = AFTER MATCH SKIP PAST LAST
ROW). The cloud job must return exactly the same rows.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
START = date(2026, 10, 12)
TRIP_GAP_MINUTES = 45

# Daily shelf price in cents for 21 days (2026-10-12 .. 2026-11-01), one store.
PRICES = {
    "P01 Coffee 12oz":        [999] * 5 + [799] * 6 + [999] * 10,                         # one clean 7-day TPR
    "P02 Greek yogurt 32oz":  [549] * 3 + [499, 449] + [449] * 4 + [549] * 3 + [499] * 3 + [549] * 6,  # two dips
    "P03 Olive oil 1L":       [1199, 1249, 1299, 1349, 1399] + [1399] * 16,               # only rises (inflation)
    "P04 Bananas lb":         [59] * 21,                                                  # flat
    "P05 Cereal 18oz":        [449] * 4 + [399] * 3 + [349] * 4 + [379, 449] + [449] * 8,  # stepped cut, 2-step return
    "P06 Ice cream 48oz":     [599] * 6 + [499] * 15,                                     # permanent cut: no recovery
}


def price_rows() -> list[tuple[str, str, int]]:
    return [(sku, (START + timedelta(days=i)).isoformat(), p) for sku, ps in PRICES.items() for i, p in enumerate(ps)]


def scan_rows() -> list[tuple[str, str, int]]:
    """Deterministic loyalty scans (card, timestamp, cents) for 8 cards over one weekend."""
    rows, x = [], 20261108
    base = datetime(2026, 10, 31, 8, 0)
    for card in range(1, 9):
        t = base + timedelta(minutes=37 * card)
        for _ in range(9 + card % 4):
            x = (x * 1103515245 + 12345) % 2**31
            gap = (3 + x % 12) if x % 5 else (50 + x % 400)      # mostly minutes apart, sometimes a new trip
            t += timedelta(minutes=gap)
            x = (x * 1103515245 + 12345) % 2**31
            rows.append((f"L{card:02d}", t.strftime("%Y-%m-%d %H:%M:%S"), 99 + x % 1200))
    return rows


# ---------- SQL (Databricks SQL / Databricks Runtime 19+) ----------
DIPS_SQL = """
SELECT sku, dip_no, start_day, cut_day, back_day, days, before_cents, promo_cents, back_cents
FROM shelf_prices
MATCH_RECOGNIZE (
  PARTITION BY sku
  ORDER BY day
  MEASURES MATCH_NUMBER()    AS dip_no,
           FIRST(day)        AS start_day,
           FIRST(down.day)   AS cut_day,
           LAST(day)         AS back_day,
           COUNT(*)          AS days,
           FIRST(price)      AS before_cents,
           LAST(down.price)  AS promo_cents,
           LAST(up.price)    AS back_cents
  ONE ROW PER MATCH
  AFTER MATCH SKIP PAST LAST ROW
  PATTERN ( strt down+ (flat | down)* up+ )
  DEFINE down AS price < PREV(price),
         flat AS price = PREV(price),
         up   AS price > PREV(price)
) AS d
ORDER BY sku, dip_no
"""

TRIPS_SQL = f"""
SELECT card, trip_no, first_scan, last_scan, scans
FROM loyalty_scans
MATCH_RECOGNIZE (
  PARTITION BY card
  ORDER BY scan_ts
  MEASURES MATCH_NUMBER() AS trip_no,
           FIRST(scan_ts) AS first_scan,
           LAST(scan_ts)  AS last_scan,
           COUNT(*)       AS scans
  ONE ROW PER MATCH
  AFTER MATCH SKIP PAST LAST ROW
  PATTERN ( strt same_trip* )
  DEFINE same_trip AS scan_ts <= PREV(scan_ts) + INTERVAL {TRIP_GAP_MINUTES} MINUTE
) AS t
ORDER BY card, trip_no
"""


# ---------- local reference: DEFINE -> one symbol per row, PATTERN -> regular expression ----------
def _partitions(rows):
    out: dict[str, list] = {}
    for key, order, value in rows:
        out.setdefault(key, []).append((order, value))
    return {k: sorted(v) for k, v in sorted(out.items())}


def dips_reference() -> list[dict]:
    res = []
    for sku, rows in _partitions(price_rows()).items():
        # PREV() of the first row is NULL, so no condition holds there: symbol '-' (only strt can take it).
        sym = "-" + "".join("D" if p < q else "F" if p == q else "U" for (_, q), (_, p) in zip(rows, rows[1:]))
        for n, m in enumerate(re.finditer(r".D+(?:F|D)*U+", sym), start=1):
            downs = [i for i in range(m.start(), m.end()) if sym[i] == "D"]   # symbols are exclusive
            res.append({"sku": sku, "dip_no": n, "start_day": rows[m.start()][0], "cut_day": rows[downs[0]][0],
                        "back_day": rows[m.end() - 1][0], "days": m.end() - m.start(),
                        "before_cents": rows[m.start()][1], "promo_cents": rows[downs[-1]][1],
                        "back_cents": rows[m.end() - 1][1]})
    return res


def trips_reference() -> list[dict]:
    res = []
    gap = timedelta(minutes=TRIP_GAP_MINUTES)
    for card, rows in _partitions(scan_rows()).items():
        ts = [datetime.fromisoformat(t) for t, _ in rows]
        sym = "-" + "".join("C" if b <= a + gap else "N" for a, b in zip(ts, ts[1:]))
        for n, m in enumerate(re.finditer(r".C*", sym), start=1):
            res.append({"card": card, "trip_no": n, "first_scan": rows[m.start()][0],
                        "last_scan": rows[m.end() - 1][0], "scans": m.end() - m.start()})
    return res


def summarize(dips: list[dict], trips: list[dict]) -> dict:
    scans = scan_rows()
    return {
        "price_rows": len(price_rows()), "skus": len(PRICES), "scan_rows": len(scans),
        "cards": len({c for c, _, _ in scans}),
        "dips": dips, "trips": trips,
        "dip_count": len(dips), "skus_with_dips": sorted({d["sku"] for d in dips}),
        "deepest_dip_pct": max(round(100 * (d["before_cents"] - d["promo_cents"]) / d["before_cents"], 1)
                               for d in dips),
        "trip_count": len(trips),
        "max_trips_per_card": max(t["trip_no"] for t in trips),
    }


def reference() -> dict:
    return summarize(dips_reference(), trips_reference())


# ---------- Spark (Databricks serverless job) ----------
def spark_result(spark) -> dict:
    spark.createDataFrame(price_rows(), "sku STRING, day STRING, price INT") \
        .selectExpr("sku", "CAST(day AS DATE) AS day", "price").createOrReplaceTempView("shelf_prices")
    spark.createDataFrame(scan_rows(), "card STRING, scan_ts STRING, cents INT") \
        .selectExpr("card", "CAST(scan_ts AS TIMESTAMP) AS scan_ts", "cents").createOrReplaceTempView("loyalty_scans")
    dips = [{**r.asDict(), "start_day": str(r.start_day), "cut_day": str(r.cut_day), "back_day": str(r.back_day)}
            for r in spark.sql(DIPS_SQL).collect()]
    trips = [{**r.asDict(), "first_scan": r.first_scan.strftime("%Y-%m-%d %H:%M:%S"),
              "last_scan": r.last_scan.strftime("%Y-%m-%d %H:%M:%S")} for r in spark.sql(TRIPS_SQL).collect()]
    for d in dips:
        d["dip_no"], d["days"] = int(d["dip_no"]), int(d["days"])
    for t in trips:
        t["trip_no"], t["scans"] = int(t["trip_no"]), int(t["scans"])
    return summarize(dips, trips)


def main(argv: list[str]) -> int:
    if len(argv) > 1:      # serverless job
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        res = {"engine": f"MATCH_RECOGNIZE on Databricks serverless (Spark {spark.version})", **spark_result(spark)}
        out = Path(argv[1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(json.dumps(res))
        print("DAY35_RESULT " + json.dumps(res))
        return 0
    res = {"engine": "Python re over DEFINE symbols (local reference)", **reference()}
    (HERE / "result.json").write_text(json.dumps(res, indent=2) + "\n")
    print(f"{res['dip_count']} price dips in {len(res['skus_with_dips'])} SKUs (deepest {res['deepest_dip_pct']}%), "
          f"{res['trip_count']} trips from {res['scan_rows']} scans on {res['cards']} cards")
    for d in res["dips"]:
        print(d)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
