"""Day 18 Google Cloud: the alias query and the expanded control query give the same deals in DuckDB, both match a
plain-Python model of the shelf-tag math, the alias query really reuses its aliases, and the cloud comparison reads
bq JSON output and rejects a wrong result (no cloud)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-18-shelf-tag-aliases"
spec = importlib.util.spec_from_file_location("shelf_tags_day18", FEATURE / "shelf_tags.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

ALIASES = st.run_local("aliases")
EXPANDED = st.run_local("expanded")


def test_both_queries_match_pure_python():
    assert ALIASES == EXPANDED == st.pure_python()
    assert len(ALIASES) == 14


def test_alias_query_reuses_aliases():
    sql = st.bigquery_sql("aliases", "p.ds")
    where = sql.split("WHERE")[1]
    assert "saving_cents >=" in where and "cents_per_kg <=" in where and "DIV(" not in where
    assert st.promo_mentions("aliases") == 1 and st.promo_mentions("expanded") == 6
    assert "`p.ds.shelf_items`" in sql and "{" not in sql


def test_deal_rule_examples():
    by_sku = {r[0]: r for r in ALIASES}
    assert "0715" not in by_sku          # cheddar: 30% off but 1229c per kg
    assert "4011" not in by_sku          # bananas: no ad discount
    assert by_sku["8001"][7] == 1054     # paper towels: 974c promo + 8.25% tax at checkout
    assert all(r[5] >= st.MIN_SAVING_CENTS and r[6] <= st.MAX_CENTS_PER_KG for r in ALIASES)


def _bq_json(rows):
    return "WARNING: --scopes\n" + json.dumps([{c: str(v) for c, v in zip(st.COLUMNS, r)} for r in rows])


def test_compare_reads_bq_output(tmp_path):
    (tmp_path / "a.json").write_text(_bq_json(ALIASES))
    rows = st.read_rows(tmp_path / "a.json")
    assert rows == ALIASES
    assert st.compare(rows, rows, ALIASES) == []
    assert st.compare(rows[:-1], rows, ALIASES)
    assert st.compare(rows, rows[::-1], ALIASES)       # order matters: best value first


def test_setup_sql():
    sql = st.bigquery_sql("setup", "p.ds")
    assert sql.startswith("CREATE TABLE `p.ds.shelf_items`") and "expiration_timestamp" in sql
    assert sql.count("STRUCT<") == 1 and "('8001', 'paper towels 6ct', 'household', 1299, 25, 1400, 825)" in sql
