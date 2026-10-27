"""Day 22 Databricks: every store report sent with valid statement-level query tags, the local DuckDB reports agree
with plain Python, and the tag rollup (what history.sql computes) attributes one statement per store."""
import importlib.util
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-22-store-query-tags"
spec = importlib.util.spec_from_file_location("store_query_tags", FEATURE / "store_query_tags.py")
qt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qt)


def python_reference(rows, store):
    agg = defaultdict(lambda: {"units": 0, "net": Decimal(0), "lines": 0})
    for r in rows:
        if r["store"] == store:
            a = agg[r["department"]]
            a["units"] += int(r["units"])
            a["net"] += Decimal(r["net_sales"])
            a["lines"] += 1
    return [{"department": d, "units": a["units"], "net_sales": float(a["net"]), "lines": a["lines"]}
            for d, a in sorted(agg.items())]


def test_local_reports_match_python():
    rows = qt.load_rows()
    local = qt.run_local(rows)
    assert set(local) == set(qt.STORES)
    for s in qt.STORES:
        assert local[s] == python_reference(rows, s)


def test_tags_follow_the_documented_limits():
    for s in qt.STORES:
        tags = qt.tags_for(s)
        assert qt.check_tags(tags) == []
        assert {t["key"]: t["value"] for t in tags} == {"store": s, "report": "daily_close", "feature": "day22"}
    assert qt.check_tags([{"key": "cost-center", "value": "x"}]) == ["bad key 'cost-center'"]
    assert qt.check_tags([{"key": "@@sys", "value": "x"}]) == ["bad key '@@sys'"]
    assert qt.check_tags([{"key": f"k{i}", "value": "v"} for i in range(21)]) == ["more than 20 tags"]


def test_rollup_attributes_one_statement_per_store_and_ignores_other_tags():
    local = qt.run_local(qt.load_rows())
    history = qt.simulated_history(local) + [
        {"query_tags": {"store": "north", "report": "adhoc"}, "produced_rows": 99},  # not tagged day22
        {"query_tags": None, "produced_rows": 5},
    ]
    roll = qt.rollup(history)
    assert [(r["store"], r["statements"], r["produced_rows"]) for r in roll] == \
        [(s, 1, len(local[s])) for s in qt.STORES]


def test_sql_is_parameterised_safely():
    assert "WHERE store = 'north'" in qt.report_sql("north")
    assert "microfeatures_day22.store_sales" in qt.report_sql("east")
    with pytest.raises(ValueError):
        qt.report_sql("north' OR '1'='1")
    ids = ["01f0a1b2-c3d4-4e5f-8a9b-0c1d2e3f4a5b"]
    h = qt.history_sql(ids)
    assert "system.query.history" in h and "query_tags['feature'] = 'day22'" in h and f"'{ids[0]}'" in h
    with pytest.raises(ValueError):
        qt.history_sql(["x'; DROP TABLE t; --"])
    assert "CREATE OR REPLACE TABLE microfeatures_day22.store_sales" in qt.table_sql(qt.load_rows()[:1])


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    rows = qt.load_rows()
    local = qt.run_local(rows)
    summary = qt.summarise(rows, local, qt.rollup(qt.simulated_history(local)))
    assert {k: result[k] for k in summary} == summary
