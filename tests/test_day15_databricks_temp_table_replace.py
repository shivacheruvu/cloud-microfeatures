"""Day 15 Databricks: the local Python gives the expected margin flags from the corrected delivery only, DuckDB runs the
very same statements (the SQL script's BEGIN/END removed) and agrees row for row, a second plain CREATE TEMP TABLE
fails where CREATE OR REPLACE succeeds, the data files are reproducible, and the cloud comparison accepts
Statement-Execution-shaped rows that match and rejects any difference or replaced-delivery row (no cloud)."""
import importlib.util
from pathlib import Path

import duckdb
import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-15-supplier-cost-restage"
spec = importlib.util.spec_from_file_location("restage_day15", FEATURE / "restage.py")
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)

DATA = rs.load()
LOCAL = rs.run_local(DATA)
BY_SKU = {r["sku"]: r for r in LOCAL}


def as_cloud(rows):  # the Statement Execution API returns every value as a string (or null)
    return [[None if v is None else (str(v).lower() if isinstance(v, bool) else str(v))
             for v in (r["sku"], r["aisle"], r["cost_cents"], r["price_cents"], r["delivery"], r["below_target"],
                       r["suggested_cents"], r["batch_rows"])] for r in rows]


def duckdb_statements():
    script = rs.sql_script(DATA)
    assert script.startswith("BEGIN") and script.endswith("END")
    body = script[len("BEGIN"):-len("END")]
    return [s.strip() for s in body.split(";") if s.strip()]


def test_data_is_reproducible():
    assert rs.make_data() == DATA


def test_expected_answers():
    assert len(LOCAL) == 12 and all(r["delivery"] == 2 and r["batch_rows"] == 12 for r in LOCAL)
    assert BY_SKU["P10"] == {"sku": "P10", "aisle": "bakery", "cost_cents": 409, "price_cents": 499, "delivery": 2,
                             "below_target": True, "suggested_cents": 549, "batch_rows": 12}
    assert BY_SKU["P02"]["below_target"] is False and BY_SKU["P02"]["suggested_cents"] is None
    assert rs.suggested(300) == 409 and rs.suggested(329) == 439      # >= 25% margin, ends in 9
    for r in LOCAL:
        if r["below_target"]:
            p = r["suggested_cents"]
            assert p % 10 == 9 and (p - r["cost_cents"]) * 4 >= p and (p - 10 - r["cost_cents"]) * 4 < p - 10
    s = rs.summarise(LOCAL, DATA)
    assert s["costs_corrected"] == ["P02", "P06", "P09"] and s["added_in_redelivery"] == ["P12"]
    assert s["first_file_wrong_flags"] == ["P06", "P09"] and len(s["below_target"]) == 7


def test_duckdb_runs_the_same_statements():
    con = duckdb.connect()
    stmts = duckdb_statements()
    assert sum(s.startswith("CREATE OR REPLACE TEMP TABLE cost_batch") for s in stmts) == 2
    for s in stmts[:-1]:
        con.execute(s)
    rows = con.execute(stmts[-1]).fetchall()
    cloud = rs.rows_to_result(as_cloud([dict(zip(("sku", "aisle", "cost_cents", "price_cents", "delivery",
                                                  "below_target", "suggested_cents", "batch_rows"), r)) for r in rows]))
    assert rs.compare(cloud, LOCAL) == []


def test_plain_create_would_fail_on_redelivery():
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE cost_batch (sku VARCHAR, cost_cents INT, delivery INT)")
    with pytest.raises(duckdb.CatalogException):
        con.execute("CREATE TEMP TABLE cost_batch (sku VARCHAR, cost_cents INT, delivery INT)")
    con.execute("INSERT INTO cost_batch VALUES ('P01', 1, 1)")
    con.execute("CREATE OR REPLACE TEMP TABLE cost_batch (sku VARCHAR, cost_cents INT, delivery INT)")
    assert con.execute("SELECT COUNT(*) FROM cost_batch").fetchone() == (0,)   # the first file's rows are gone


def test_compare_accepts_match_and_rejects_differences():
    assert rs.compare(rs.rows_to_result(as_cloud(LOCAL)), LOCAL) == []
    assert rs.compare(rs.rows_to_result(as_cloud(LOCAL))[1:], LOCAL)                     # missing row
    assert rs.compare(rs.rows_to_result(as_cloud(LOCAL) + as_cloud(LOCAL)[:1]), LOCAL)   # duplicate row
    off = as_cloud(LOCAL)
    off[0][2] = str(int(off[0][2]) + 1)                                                   # cost off by a cent
    assert rs.compare(rs.rows_to_result(off), LOCAL)
    old = as_cloud(LOCAL)
    old[3][4] = "1"                                                                       # a replaced-delivery row
    assert any("replaced delivery" in e for e in rs.compare(rs.rows_to_result(old), LOCAL))
