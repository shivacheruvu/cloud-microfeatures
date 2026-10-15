"""Day 10 Databricks: local substitutes ranking, no ties at the top-2 boundary (so EXACT NEAREST 2 is unique), and
the cloud comparison accepts Statement-Execution-shaped rows that match and rejects a wrong substitute (no cloud)."""
import importlib.util
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-10-out-of-stock-substitutes"
spec = importlib.util.spec_from_file_location("substitutes_day10", FEATURE / "substitutes.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)

PRODS = sb.products()
LOCAL = sb.run_local(PRODS)


def test_two_substitutes_per_out_of_stock_item():
    oos = [p["sku"] for p in PRODS if not p["in_stock"]]
    assert sorted({r["oos_sku"] for r in LOCAL}) == sorted(oos)
    assert len(LOCAL) == 2 * len(oos)
    got = {(r["oos_sku"], r["rank"]): (r["sub_sku"], r["score"]) for r in LOCAL}
    assert got[("O01", 1)] == ("P03", 5)       # organic soy milk beats cheaper non-organic milks
    assert got[("O03", 1)] == ("P09", 1) and got[("O03", 2)] == ("P10", 3)
    assert got[("O05", 1)] == ("P17", 2)
    assert got[("O05", 2)][1] >= sb.NO_CLOSE_MATCH   # only one sparkling water on the shelf


def test_matches_pure_python_and_no_ties_at_boundary():
    shelf = [p for p in PRODS if p["in_stock"]]
    for q in (p for p in PRODS if not p["in_stock"]):
        scored = sorted((sb.score(q, t), t["sku"]) for t in shelf)
        assert scored[1][0] < scored[2][0], q["sku"]   # rank 2 and 3 differ: EXACT top-2 is unique
        assert scored[0][0] < scored[1][0], q["sku"]   # rank order is unique too
        mine = [(r["score"], r["sub_sku"]) for r in LOCAL if r["oos_sku"] == q["sku"]]
        assert mine == scored[:2]


def cloud_rows(rows):
    return [[r["oos_sku"], r["oos_product"], str(r["rank"]), r["sub_sku"], r["sub_product"], str(r["score"])]
            for r in rows]


def test_compare_accepts_match_and_rejects_wrong_substitute():
    assert sb.compare(sb.rows_to_result(cloud_rows(LOCAL)), LOCAL) == []
    bad = cloud_rows(LOCAL)
    bad[1][3] = "P01"
    errors = sb.compare(sb.rows_to_result(bad), LOCAL)
    assert len(errors) == 1 and errors[0].startswith("O01")
    assert sb.compare(sb.rows_to_result(cloud_rows(LOCAL))[:-1], LOCAL)   # a missing row is an error


def test_sql_uses_nearest_by():
    sql = sb.sql_statements(PRODS)
    assert "EXACT NEAREST 2 BY DISTANCE" in sql["databricks"]
    assert "{products}" not in sql["databricks"] and "'O05'" in sql["databricks"]
    assert " ON " not in sql["databricks"].split("INNER JOIN")[1].split(")")[0]
