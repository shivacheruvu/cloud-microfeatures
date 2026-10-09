"""Day 4 Databricks: the local window-function reference agrees with a plain-Python rolling sum, and the metric view
YAML declares the window measures the cloud run queries."""
import importlib.util
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-04-rolling-sales-metric-view"
spec = importlib.util.spec_from_file_location("rolling_sales", FEATURE / "rolling_sales.py")
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)


def python_reference(rows, by_store=True):
    daily = defaultdict(int)
    for r in rows:
        daily[(r["store"] if by_store else "all", r["sale_date"])] += int(r["units"])
    out = []
    for store, d in sorted(daily):
        day = date.fromisoformat(d)
        window = [(day - timedelta(k)).isoformat() for k in range(1, 8)]  # the 7 days before, not the day itself
        prior = [daily[(store, w)] for w in window if (store, w) in daily]
        out.append({"store": store, "sale_date": d, "units_sold": daily[(store, d)],
                    "units_prior_7d": sum(prior) if prior else None, "open_days_prior_7d": len(prior),
                    "units_mtd": sum(v for (s, dd), v in daily.items() if s == store and dd <= d)})
    return out


def test_local_matches_python_reference():
    rows = rs.load_rows()
    local = rs.run_local(rows)
    assert local["by_store"] == python_reference(rows)
    assert local["all_stores"] == python_reference(rows, by_store=False)


def test_closed_day_is_a_gap_not_a_zero():
    north = {r["sale_date"]: r for r in rs.run_local(rs.load_rows())["by_store"] if r["store"] == "north"}
    assert "2026-09-09" not in north
    assert north["2026-09-12"]["open_days_prior_7d"] == 6
    assert north["2026-09-20"]["open_days_prior_7d"] == 7


def test_spikes_are_the_promo_days():
    spikes = rs.spikes(rs.run_local(rs.load_rows())["by_store"])
    assert [(s["store"], s["sale_date"]) for s in spikes] == [
        ("north", "2026-09-18"), ("south", "2026-09-12"), ("south", "2026-09-25")]


def test_metric_view_declares_window_measures():
    sql = rs.metric_view_sql()
    assert "WITH METRICS" in sql and "LANGUAGE YAML" in sql and "version: 1.1" in sql
    assert sql.count("range: trailing 7 day") == 2 and "range: cumulative" in sql
    assert sql.count("order: sale_date") == 3 and sql.count("semiadditive: last") == 3
    q = rs.queries()
    assert list(q) == ["by_store", "all_stores"]
    for name in ("units_sold", "units_prior_7d", "open_days_prior_7d", "units_mtd"):
        assert all(f"MEASURE({name})" in s for s in q.values())
    assert "CREATE OR REPLACE TABLE microfeatures_day04.daily_units" in rs.table_sql(rs.load_rows()[:1])


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    summary = rs.summarise(rs.run_local(rs.load_rows()))
    assert {k: result[k] for k in summary} == summary
