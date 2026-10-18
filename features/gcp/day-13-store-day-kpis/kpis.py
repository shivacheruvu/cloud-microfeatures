"""Day 13 (Google Cloud): store-day KPIs with BigQuery multi-level aggregation (Preview, 2026-07-08). Average daily
revenue, best day, average weekend-day revenue, average basket value and size, and the average shelf price counted
once per SKU, each in one aggregate call such as AVG(SUM(revenue) GROUP BY sale_date), for three stores and two
weeks of basket lines.

  python kpis.py --make-data       # regenerate data/lines.csv and data/prices.csv (deterministic)
  python kpis.py --local           # DuckDB computes the same KPIs with explicit nested GROUP BYs; writes result.json
  python kpis.py --sql             # print the BigQuery statement (data inlined)
  python kpis.py --compare OUT     # check bq's JSON output against the local KPIs

The cloud check:
  * errors: a missing or extra store, or any KPI more than 0.01 cents away from the local value
  * warnings: none
Data is made up; prices are in cents so every KPI can be compared exactly.
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINES_CSV = HERE / "data" / "lines.csv"
PRICES_CSV = HERE / "data" / "prices.csv"
STORES = {"S1": "Downtown Market", "S2": "Riverside Fresh", "S3": "Southgate Grocer"}
START = date(2026, 9, 28)  # a Monday; 14 days to Sunday 2026-10-11
DAYS = 14
# sku -> base shelf price in cents; stores price within a few cents of it
SKUS = {"bananas": 59, "milk": 389, "eggs": 449, "bread": 329, "coffee": 1099, "oat_milk": 479}
POPULAR = ["bananas"] * 5 + ["milk"] * 4 + ["eggs"] * 3 + ["bread"] * 3 + ["oat_milk"] * 2 + ["coffee"]
STORE_SIZE = {"S1": 6, "S2": 4, "S3": 3}       # weekday baskets per day
WEEKEND_LIFT = {"S1": 2, "S2": 4, "S3": 1}     # extra baskets on Saturday and Sunday
KPIS = ("revenue", "avg_daily_revenue", "best_day_revenue", "avg_weekend_day_revenue", "avg_basket_value",
        "avg_items_per_basket", "avg_shelf_price", "naive_avg_shelf_price")
TOLERANCE = 0.01


def make_data() -> tuple[list[dict], list[dict]]:
    rng = random.Random(13)
    prices = [{"store": s, "sku": k, "shelf_price": str(base + rng.choice([-20, -10, 0, 0, 10, 30]))}
              for s in STORES for k, base in SKUS.items()]
    lines = []
    for s in STORES:
        n = 0
        for d in range(DAYS):
            day = START + timedelta(days=d)
            baskets = STORE_SIZE[s] + (WEEKEND_LIFT[s] if day.weekday() >= 5 else 0) + rng.choice([-1, 0, 0, 1])
            for _ in range(baskets):
                n += 1
                bid = f"{s}-{n:03d}"
                for sku in sorted(set(rng.sample(POPULAR, rng.randint(1, 4)))):
                    lines.append({"store": s, "sale_date": day.isoformat(), "basket_id": bid, "sku": sku,
                                  "qty": str(rng.choice([1, 1, 1, 2, 2, 3]))})
    return lines, prices


def _read(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load() -> tuple[list[dict], list[dict]]:
    return _read(LINES_CSV), _read(PRICES_CSV)


def bigquery_sql(lines: list[dict], prices: list[dict]) -> str:
    ls = ",\n    ".join(f"STRUCT('{l['store']}' AS store, DATE '{l['sale_date']}' AS sale_date, '{l['basket_id']}' "
                        f"AS basket_id, '{l['sku']}' AS sku, {int(l['qty'])} AS qty)" for l in lines)
    ps = ",\n    ".join(f"STRUCT('{p['store']}' AS store, '{p['sku']}' AS sku, {int(p['shelf_price'])} AS shelf_price)"
                        for p in prices)
    return (HERE / "kpis.sql").read_text().replace("{lines}", ls).replace("{prices}", ps)


# ---------- local: DuckDB with the two levels written out as nested GROUP BYs
LOCAL_SQL = """
WITH sales AS (
  SELECT l.store, l.sale_date, dayofweek(l.sale_date) IN (0, 6) AS is_weekend, l.basket_id, l.sku, l.qty,
         p.shelf_price, l.qty * p.shelf_price AS revenue
  FROM lines l JOIN prices p USING (store, sku)
), by_day AS (
  SELECT store, sale_date, is_weekend, SUM(revenue) AS rev FROM sales GROUP BY ALL
), by_basket AS (
  SELECT store, basket_id, SUM(revenue) AS rev, SUM(qty) AS items FROM sales GROUP BY ALL
), by_sku AS (
  SELECT store, sku, ANY_VALUE(shelf_price) AS price FROM sales GROUP BY ALL
)
SELECT s.store,
       (SELECT COUNT(*) FROM by_day d WHERE d.store = s.store) AS days,
       (SELECT COUNT(*) FROM by_basket b WHERE b.store = s.store) AS baskets,
       SUM(revenue) AS revenue,
       (SELECT AVG(rev) FROM by_day d WHERE d.store = s.store) AS avg_daily_revenue,
       (SELECT MAX(rev) FROM by_day d WHERE d.store = s.store) AS best_day_revenue,
       (SELECT AVG(rev) FROM by_day d WHERE d.store = s.store AND d.is_weekend) AS avg_weekend_day_revenue,
       (SELECT AVG(rev) FROM by_basket b WHERE b.store = s.store) AS avg_basket_value,
       (SELECT AVG(items) FROM by_basket b WHERE b.store = s.store) AS avg_items_per_basket,
       (SELECT AVG(price) FROM by_sku k WHERE k.store = s.store) AS avg_shelf_price,
       AVG(shelf_price) AS naive_avg_shelf_price
FROM sales s
GROUP BY s.store
ORDER BY s.store
"""


def run_local(lines: list[dict], prices: list[dict]) -> dict[str, dict]:
    import duckdb

    con = duckdb.connect()
    con.execute("CREATE TABLE lines (store VARCHAR, sale_date DATE, basket_id VARCHAR, sku VARCHAR, qty BIGINT)")
    con.executemany("INSERT INTO lines VALUES (?, ?, ?, ?, ?)",
                    [[l["store"], l["sale_date"], l["basket_id"], l["sku"], int(l["qty"])] for l in lines])
    con.execute("CREATE TABLE prices (store VARCHAR, sku VARCHAR, shelf_price BIGINT)")
    con.executemany("INSERT INTO prices VALUES (?, ?, ?)", [[p["store"], p["sku"], int(p["shelf_price"])] for p in prices])
    cur = con.execute(LOCAL_SQL)
    cols = [c[0] for c in cur.description]
    return {r[0]: {c: (float(v) if c in KPIS else v) for c, v in zip(cols[1:], r[1:])} for r in cur.fetchall()}


# ---------- BigQuery output
def read_bq(path: str) -> list[dict]:
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a warning line before the JSON
    try:
        return json.loads(text[start:]) if start >= 0 else []
    except Exception as e:  # noqa: BLE001 - any shape surprise is reported, redacted, as an annotation
        raw = text[max(start, 0):max(start, 0) + 500].replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {e}; {raw!r}")
        raise SystemExit(1)


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def compare(cloud: list[dict], local: dict[str, dict]) -> list[str]:
    got = {c.get("store"): c for c in cloud}
    errors = [f"{s}: no output row" for s in local if s not in got]
    errors += [f"{s}: unexpected row" for s in got if s not in local]
    for s, want in local.items():
        if s not in got:
            continue
        for k in KPIS:
            v = _num(got[s].get(k))
            if v is None or abs(v - want[k]) > TOLERANCE:
                errors.append(f"{s} {k}: BigQuery {got[s].get(k)!r} vs local {want[k]:.4f}")
    return errors


def summarise(local: dict[str, dict]) -> dict:
    stores = {s: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()} for s, m in local.items()}
    lift = {s: round(100 * (m["avg_weekend_day_revenue"] / m["avg_daily_revenue"] - 1), 1) for s, m in local.items()}
    top = max(lift, key=lift.get)
    skew = {s: round(m["naive_avg_shelf_price"] - m["avg_shelf_price"], 2) for s, m in local.items()}
    return {
        "stores": len(local), "days": DAYS,
        "baskets": sum(m["baskets"] for m in local.values()),
        "kpis": stores, "weekend_lift_pct": lift,
        "naive_price_skew_cents": skew,
        "headline": f"{STORES[top]} has the biggest weekend lift (+{lift[top]}% vs its average day); "
                    f"avg basket ${local['S1']['avg_basket_value'] / 100:.2f} / "
                    f"${local['S2']['avg_basket_value'] / 100:.2f} / ${local['S3']['avg_basket_value'] / 100:.2f}; "
                    f"a plain AVG over the join misstates the shelf price by up to "
                    f"{max(abs(v) for v in skew.values()):.0f} cents",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        for path, rows in zip((LINES_CSV, PRICES_CSV), make_data()):
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        raise SystemExit(0)
    lines, prices = load()
    if mode == "--sql":
        print(bigquery_sql(lines, prices))
    elif mode == "--compare":
        cloud = read_bq(sys.argv[2])
        local = run_local(lines, prices)
        try:
            errors = compare(cloud, local)
        except Exception as e:  # report what BigQuery returned (data rows only: no project or account details)
            print(f"::error title=Multi-level aggregation output not understood::{type(e).__name__}: {e}; "
                  f"{len(cloud)} row(s), first: {json.dumps(cloud[:2])[:700]}")
            raise SystemExit(1)
        if errors:
            print(f"::error title=Multi-level aggregation differs from local::{len(errors)} difference(s): "
                  f"{json.dumps(errors)[:800]}")
            raise SystemExit(1)
        s = summarise(local)
        brief = {st: {k: m[k] for k in ("avg_daily_revenue", "best_day_revenue", "avg_weekend_day_revenue",
                                         "avg_basket_value", "avg_shelf_price")} for st, m in s["kpis"].items()}
        print("::notice title=Google Cloud day 13::Multi-level aggregation (AVG(SUM(...) GROUP BY ...), MAX(SUM ...), "
              "a filtered inner SUM, AVG(ANY_VALUE ...)) ran on BigQuery and matched local DuckDB nested GROUP BYs for "
              f"{len(local)} stores x {len(KPIS)} KPIs: {s['headline']}; cents {json.dumps(brief)[:600]}")
    else:
        summary = summarise(run_local(lines, prices))
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB nested GROUP BYs (local) / BigQuery "
                                                      "multi-level aggregation (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
