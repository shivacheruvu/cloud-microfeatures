"""Day 9 Databricks: the local DuckDB parse matches the declared verdicts and tables, and the parse_sql comparison
accepts plausible output shapes and rejects wrong verdicts or missing tables (no cloud)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-09-report-query-audit"
spec = importlib.util.spec_from_file_location("audit_day09", FEATURE / "audit.py")
au = importlib.util.module_from_spec(spec)
spec.loader.exec_module(au)

REPS = au.reports()
LOCAL = au.run_local(REPS)


def test_local_matches_declared():
    assert au.check_local(LOCAL, REPS) == []
    s = au.summarise(LOCAL)
    assert (s["queries"], s["parse"], s["broken"], s["tables"]) == (8, 5, 3, 5)
    assert s["lineage"]["scans"] == ["weekly_revenue", "top_products", "promo_lift", "basket_size"]


def test_cte_name_is_not_a_table():
    promo = next(l for l in LOCAL if l["name"] == "promo_lift")
    assert promo["tables"] == ["promotions", "scans"]


def fake_cloud(style):
    rows = []
    for r in REPS:
        if not r["parses"]:
            out = ({"error": {"errorClass": "PARSE_SYNTAX_ERROR", "message": "Syntax error at or near 'FROM'"}}
                   if style == "nested" else {"status": "ERROR", "message": "[PARSE_SYNTAX_ERROR] at end of input",
                                              "errors": None})
        else:
            refs = [{"catalog": None, "schema": None, "name": t} for t in r["tables"]]
            out = ({"statement_type": "QUERY", "table_references": refs, "error": None} if style == "nested"
                   else {"type": "SELECT", "tables": [f"spark_catalog.default.{t}" for t in r["tables"]]})
        rows.append([r["name"], json.dumps(out)])
    return au.parse_cloud(rows)


def test_compare_accepts_both_shapes():
    assert au.compare(fake_cloud("nested"), REPS) == []
    assert au.compare(fake_cloud("flat"), REPS) == []


def test_compare_rejects_wrong_verdict_and_missing_table():
    cloud = fake_cloud("nested")
    cloud["price_list"] = {"statement_type": "QUERY", "table_references": [{"name": "products"}]}
    cloud["stockouts"] = {"statement_type": "QUERY", "table_references": [{"name": "inventory"}]}
    cloud["weekly_revenue"] = None
    errors = au.compare(cloud, REPS)
    assert len(errors) == 3
    assert any("stockouts" in e and "stores" in e for e in errors)


def test_table_match_is_whole_word():
    assert au.mentions({"t": ["main.grocery.stores"]}, "stores")
    assert not au.mentions({"t": ["store_scans"]}, "scans")


def test_sql_inlines_every_query():
    sql = au.sql_statement(REPS)
    assert "parse_sql(query)" in sql and "{reports}" not in sql
    assert all(r["sql"] in sql for r in REPS)


def test_observed_parse_sql_shape():
    # shape seen in the first cloud run: one object per statement with parse_success
    bad = [{"start": 1, "length": 55, "parse_success": False,
            "error": {"errorClass": "PARSE_SYNTAX_ERROR", "sqlState": "42601"}}]
    good = [{"start": 1, "length": 40, "parse_success": True, "tables": [{"name": "scans"}]}]
    assert au.is_broken(bad) and not au.is_broken(good)
