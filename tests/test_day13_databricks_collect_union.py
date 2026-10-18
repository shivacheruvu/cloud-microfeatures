"""Day 13 Databricks: the local set unions give the expected basket and aisle allergen sets, DuckDB's long-hand
list_distinct(flatten(list(...))) agrees, the data files are reproducible, the SQL is built from them, and the cloud
comparison accepts Statement-Execution-shaped rows that match and rejects any difference (no cloud)."""
import importlib.util
import json
from pathlib import Path

import duckdb

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-13-basket-allergens"
spec = importlib.util.spec_from_file_location("allergens_day13", FEATURE / "allergens.py")
al = importlib.util.module_from_spec(spec)
spec.loader.exec_module(al)

PRODUCTS, LINES = al.load()
LOCAL = al.run_local(PRODUCTS, LINES)
BY_KEY = {(r["level"], r["key"]): r for r in LOCAL}


def test_data_is_reproducible():
    assert al.make_data() == (PRODUCTS, LINES)


def test_expected_sets():
    assert BY_KEY[("basket", "B01")]["allergens"] == [] and BY_KEY[("basket", "B07")]["allergens"] == []
    assert BY_KEY[("basket", "B08")]["allergens"] == ["gluten", "milk", "peanuts", "soy", "tree_nuts"]
    assert BY_KEY[("basket", "B11")]["n_allergens"] == 7
    assert BY_KEY[("aisle", "produce")]["allergens"] == [] and BY_KEY[("aisle", "produce")]["nut_free"]
    assert not BY_KEY[("aisle", "bakery")]["nut_free"]
    s = al.summarise(LOCAL)
    assert (s["baskets"], s["aisles"]) == (12, 5)
    assert s["allergen_free_baskets"] == ["B01", "B07"]
    assert s["nut_baskets"] == ["B02", "B03", "B08", "B10", "B11"]
    assert next(iter(s["baskets_per_allergen"])) == "gluten" and s["baskets_per_allergen"]["gluten"] == 8


def test_duckdb_long_hand_agrees():
    con = duckdb.connect()
    con.execute("CREATE TABLE products (sku VARCHAR, aisle VARCHAR, allergens VARCHAR[])")
    con.executemany("INSERT INTO products VALUES (?, ?, ?)", [[p["sku"], p["aisle"], al.tags(p)] for p in PRODUCTS])
    con.execute("CREATE TABLE lines (basket_id VARCHAR, sku VARCHAR)")
    con.executemany("INSERT INTO lines VALUES (?, ?)", [[l["basket_id"], l["sku"]] for l in LINES])
    rows = con.execute("""
        SELECT 'basket', basket_id, list_sort(list_distinct(flatten(list(allergens))))
        FROM lines JOIN products USING (sku) GROUP BY basket_id
        UNION ALL
        SELECT 'aisle', aisle, list_sort(list_distinct(flatten(list(allergens)))) FROM products GROUP BY aisle
    """).fetchall()
    assert {(lv, k): list(a) for lv, k, a in rows} == {k: r["allergens"] for k, r in BY_KEY.items()}


def cloud_rows(rows):  # the Statement Execution API returns every value as a string (or null)
    return [[r["level"], r["key"], json.dumps(r["allergens"]), str(r["n_allergens"]),
             str(r["nut_free"]).lower(), "true"] for r in rows]


def test_compare_accepts_match_and_rejects_differences():
    assert al.compare(al.rows_to_result(cloud_rows(LOCAL)), LOCAL) == []
    assert al.compare(al.rows_to_result(cloud_rows(LOCAL))[1:], LOCAL)            # missing group
    off = cloud_rows(LOCAL)
    i = next(n for n, r in enumerate(off) if r[1] == "B08")
    off[i][2] = json.dumps(["gluten", "milk", "peanuts", "soy"])                  # lost an allergen
    assert al.compare(al.rows_to_result(off), LOCAL)
    off = cloud_rows(LOCAL)
    off[0][5] = "false"                                                           # disagrees with long-hand
    assert al.compare(al.rows_to_result(off), LOCAL)


def test_sql_uses_collect_union():
    sql = al.sql_statements(PRODUCTS, LINES)["databricks"]
    assert "collect_union(p.allergens)" in sql and "collect_union(allergens)" in sql
    assert "array_distinct(flatten(collect_list(" in sql
    assert "CAST(array() AS ARRAY<STRING>)" in sql and "array('gluten', 'milk', 'eggs', 'tree_nuts')" in sql
    assert "{products}" not in sql and "{lines}" not in sql and ";" not in sql
