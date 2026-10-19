"""Day 14 Databricks: the local Python gives the expected current prices and aisle top sellers, DuckDB runs the very
same QUALIFY query (only the inline VALUES are parenthesised) and agrees row for row, the data files are reproducible,
and the cloud comparison accepts Statement-Execution-shaped rows that match and rejects any difference (no cloud)."""
import importlib.util
from pathlib import Path

import duckdb

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-14-current-shelf-prices"
spec = importlib.util.spec_from_file_location("shelf_day14", FEATURE / "shelf.py")
sh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sh)

TAGS, SALES = sh.load()
LOCAL = sh.run_local(TAGS, SALES)
BY_KEY = {(r["kind"], r["grp"], r["sku"]): r for r in LOCAL}


def as_cloud(rows):  # the Statement Execution API returns every value as a string (or null)
    return [[None if v is None else str(v) for v in (r["kind"], r["grp"], r["sku"], r["value"], r["detail"], r["n"],
                                                     r["prev"])] for r in rows]


def test_data_is_reproducible():
    assert sh.make_data() == (TAGS, SALES)


def test_expected_answers():
    prices = [r for r in LOCAL if r["kind"] == "price"]
    assert len(TAGS) == 87 and len(prices) == 33                     # one row per store and SKU survives
    assert BY_KEY[("price", "S1", "P07")] == {"kind": "price", "grp": "S1", "sku": "P07", "value": 299,
                                                   "detail": "2026-10-17", "n": 4, "prev": 259}
    assert BY_KEY[("price", "S3", "P07")]["prev"] is None             # never changed
    tops = {k: r for k, r in BY_KEY.items() if k[0] == "top_seller"}
    assert len(tops) == 9                                             # 4 aisles x 2, plus the produce tie
    assert tops[("top_seller", "produce", "P05")]["detail"] == tops[("top_seller", "produce", "P06")]["detail"] == "2"
    assert tops[("top_seller", "produce", "P04")]["value"] == 231
    s = sh.summarise(LOCAL, TAGS)
    assert s["rows_dropped_by_qualify"] == 54 and s["widest_store_spread"] == {"sku": "P07", "cents": 120}


def test_duckdb_runs_the_same_qualify_query():
    sql = sh.sql_statements(TAGS, SALES)["databricks"]
    duck_sql = sql.replace("FROM VALUES\n", "FROM (VALUES\n").replace("\n  AS t(", ") AS t(")
    assert duck_sql.count("(VALUES") == 2
    rows = duckdb.sql(duck_sql).fetchall()
    cloud = sh.rows_to_result([[None if v is None else str(v) for v in r] for r in rows])
    assert sh.compare(cloud, LOCAL) == []


def test_compare_accepts_match_and_rejects_differences():
    assert sh.compare(sh.rows_to_result(as_cloud(LOCAL)), LOCAL) == []
    assert sh.compare(sh.rows_to_result(as_cloud(LOCAL))[1:], LOCAL)                     # missing row
    assert sh.compare(sh.rows_to_result(as_cloud(LOCAL) + as_cloud(LOCAL)[:1]), LOCAL)   # duplicate row
    off = as_cloud(LOCAL)
    off[0][3] = str(int(off[0][3]) + 1)                                                   # price off by a cent
    assert sh.compare(sh.rows_to_result(off), LOCAL)
    off = as_cloud(LOCAL)
    i = next(n for n, r in enumerate(off) if r[6] is not None)
    off[i][6] = None                                                                      # lost the previous price
    assert sh.compare(sh.rows_to_result(off), LOCAL)


def test_sql_uses_qualify():
    sql = sh.sql_statements(TAGS, SALES)["databricks"]
    assert sql.count("QUALIFY ") == 2
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY store, sku ORDER BY effective_date DESC) = 1" in sql
    assert "QUALIFY RANK() OVER (PARTITION BY aisle ORDER BY units DESC) <= 2" in sql
    assert "SUM(qty)" not in sql.split("QUALIFY RANK()")[1]   # Databricks QUALIFY rejects aggregates
    assert "{tags}" not in sql and "{sales}" not in sql and ";" not in sql
