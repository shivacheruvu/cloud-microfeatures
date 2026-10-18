"""Day 13 Google Cloud: the data files are reproducible, the local DuckDB KPIs agree with a pure-Python
recomputation, the BigQuery SQL uses multi-level aggregation with the data inlined, and the comparison accepts
bq-shaped output that matches and rejects any difference (no cloud)."""
import importlib.util
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-13-store-day-kpis"
spec = importlib.util.spec_from_file_location("kpis_day13", FEATURE / "kpis.py")
kp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kp)

LINES, PRICES = kp.load()
LOCAL = kp.run_local(LINES, PRICES)


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs)


def test_data_is_reproducible():
    assert kp.make_data() == (LINES, PRICES)


def test_local_matches_pure_python():
    price = {(p["store"], p["sku"]): int(p["shelf_price"]) for p in PRICES}
    for s in kp.STORES:
        rows = [l for l in LINES if l["store"] == s]
        day, wkend, bask, items = defaultdict(int), {}, defaultdict(int), defaultdict(int)
        for l in rows:
            rev = int(l["qty"]) * price[(s, l["sku"])]
            day[l["sale_date"]] += rev
            wkend[l["sale_date"]] = date.fromisoformat(l["sale_date"]).weekday() >= 5
            bask[l["basket_id"]] += rev
            items[l["basket_id"]] += int(l["qty"])
        m = LOCAL[s]
        assert m["days"] == 14 and m["baskets"] == len(bask)
        assert abs(m["avg_daily_revenue"] - mean(day.values())) < 1e-9
        assert m["best_day_revenue"] == max(day.values())
        assert abs(m["avg_weekend_day_revenue"] - mean(v for d, v in day.items() if wkend[d])) < 1e-9
        assert abs(m["avg_basket_value"] - mean(bask.values())) < 1e-9
        assert abs(m["avg_items_per_basket"] - mean(items.values())) < 1e-9
        skus = {l["sku"] for l in rows}
        assert abs(m["avg_shelf_price"] - mean(price[(s, k)] for k in skus)) < 1e-9
        assert abs(m["naive_avg_shelf_price"] - mean(price[(s, l["sku"])] for l in rows)) < 1e-9


def test_summary():
    s = kp.summarise(LOCAL)
    assert (s["stores"], s["days"], s["baskets"]) == (3, 14, 208)
    assert max(s["weekend_lift_pct"], key=s["weekend_lift_pct"].get) == "S2"
    assert all(v < 0 for v in s["naive_price_skew_cents"].values())  # cheap bananas dominate the plain average


def bq_rows(local):  # bq --format=json prints numbers as strings
    return [{"store": st, **{k: str(m[k]) for k in kp.KPIS}} for st, m in local.items()]


def test_compare_accepts_match_and_rejects_differences(tmp_path):
    out = tmp_path / "bq.json"
    out.write_text("WARNING: --scopes ignored\n" + json.dumps(bq_rows(LOCAL)))
    assert kp.compare(kp.read_bq(str(out)), LOCAL) == []
    assert kp.compare(bq_rows(LOCAL)[1:], LOCAL)                       # missing store
    off = bq_rows(LOCAL)
    off[0]["avg_shelf_price"] = off[0]["naive_avg_shelf_price"]        # forgot the ANY_VALUE level
    assert kp.compare(off, LOCAL)
    off = bq_rows(LOCAL)
    off[1]["avg_weekend_day_revenue"] = None
    assert kp.compare(off, LOCAL)


def test_sql_uses_multi_level_aggregation():
    sql = kp.bigquery_sql(LINES, PRICES)
    for frag in ("AVG(SUM(revenue) GROUP BY sale_date)", "MAX(SUM(revenue) GROUP BY sale_date)",
                 "AVG(SUM(IF(is_weekend, revenue, NULL)) GROUP BY sale_date)", "AVG(ANY_VALUE(shelf_price) GROUP BY sku)"):
        assert frag in sql
    assert "{lines}" not in sql and "{prices}" not in sql and sql.count("STRUCT(") == len(LINES) + len(PRICES)
    assert "HAVING" not in "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))  # rejected on 2026-10-17: "Function avg has both having expression and group by list"
    assert ";" not in sql and len(sql) < 1_000_000
