"""Day 21 Databricks: the local DuckDB measures agree with plain Python at every markdown level, the metric view YAML
declares a `markdown` parameter used in its measures, and the queries bind it as a table-valued-function argument."""
import importlib.util
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-21-markdown-what-if"
spec = importlib.util.spec_from_file_location("markdown_what_if", FEATURE / "markdown_what_if.py")
mw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mw)


def python_reference(rows, markdown):
    agg = defaultdict(lambda: {"units": 0, "net": Decimal(0), "margin": Decimal(0), "below": 0})
    for r in rows:
        price = (Decimal(1) - Decimal(markdown)) * Decimal(r["shelf_price"])
        price = price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        a, units = agg[r["department"]], int(r["units"])
        a["units"] += units
        a["net"] += price * units
        a["margin"] += (price - Decimal(r["unit_cost"])) * units
        a["below"] += price < Decimal(r["unit_cost"])
    return [{"department": d, "markdown": float(markdown), "units": a["units"], "net_sales": float(a["net"]),
             "margin": float(a["margin"]), "lines_below_cost": a["below"]} for d, a in sorted(agg.items())]


def test_local_matches_python_reference():
    rows = mw.load_rows()
    local = mw.run_local(rows)
    for m in mw.MARKDOWNS:
        assert local[f"markdown_{m}"] == python_reference(rows, m)
    assert local["default"] == [mw.total(python_reference(rows, "0.0"))]


def test_markdown_lowers_sales_and_margin_monotonically():
    local = mw.run_local(mw.load_rows())
    totals = [mw.total(local[f"markdown_{m}"]) for m in mw.MARKDOWNS]
    assert all(a["net_sales"] > b["net_sales"] and a["margin"] > b["margin"] for a, b in zip(totals, totals[1:]))
    assert [t["lines_below_cost"] for t in totals] == [0, 0, 2, 3]
    assert mw.deepest_safe_markdown(local) == {"bakery": "30%", "dairy": "10%", "produce": "20%"}


def test_metric_view_declares_a_parameter():
    sql = mw.metric_view_sql()
    yaml = sql.split("$$")[1]
    assert "WITH METRICS" in sql and "LANGUAGE YAML" in sql and "version: 1.1" in yaml
    assert "parameters:\n  - name: markdown\n    data_type: double\n    default: 0\n" in yaml
    assert yaml.count("(1 - markdown)") == 3  # used by net_sales, margin and lines_below_cost
    assert "microfeatures_day21.week_sales" in yaml


def test_queries_bind_the_parameter_at_query_time():
    q = mw.queries()
    assert list(q) == ["by_department", "default"]
    assert "markdown_metrics(markdown => {markdown})" in q["by_department"]
    assert "markdown_metrics\n" in q["default"] and "=>" not in q["default"]
    for name in ("units", "net_sales", "margin", "lines_below_cost"):
        assert all(f"MEASURE({name})" in s for s in q.values())
    assert "CREATE OR REPLACE TABLE microfeatures_day21.week_sales" in mw.table_sql(mw.load_rows()[:1])


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    rows = mw.load_rows()
    summary = mw.summary_for(rows, mw.run_local(rows))
    assert {k: result[k] for k in summary} == summary
