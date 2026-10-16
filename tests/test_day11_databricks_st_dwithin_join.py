"""Day 11 Databricks: local delivery-radius pairs match plain Python, no home sits within 5 m of a radius boundary
(so float rounding can't move a pair in or out), and the cloud comparison accepts Statement-Execution-shaped rows
that match and rejects a missing or extra pair (no cloud)."""
import importlib.util
import math
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-11-delivery-radius"
spec = importlib.util.spec_from_file_location("coverage_day11", FEATURE / "coverage.py")
cv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv)

STORES, HOMES = cv.load()
LOCAL = cv.run_local(STORES, HOMES)


def test_data_is_reproducible_and_clear_of_boundaries():
    stores, homes = cv.make_data()
    assert stores == STORES and homes == HOMES
    for h in HOMES:
        for s in STORES:
            assert abs(math.hypot(h["x_m"] - s["x_m"], h["y_m"] - s["y_m"]) - cv.RADIUS_M) >= cv.BOUNDARY_GAP_M


def test_local_matches_pure_python():
    assert cv.compare(LOCAL, cv.pure_python(STORES, HOMES)) == []
    assert all(r["dist_m"] <= cv.RADIUS_M for r in LOCAL)


def test_summary_numbers():
    s = cv.summarise(LOCAL, STORES, HOMES)
    assert s["homes"] == 60 and s["pairs"] == 49
    assert s["homes_covered"] == 39 and s["homes_uncovered"] == 21 and s["homes_with_choice"] == 10
    assert sum(t["homes_in_radius"] for t in s["table"]) == s["pairs"]
    assert sum(t["nearest_store_for"] for t in s["table"]) == s["homes_covered"]


def cloud_rows(rows):
    return [[r["home_id"], r["store_id"], str(r["dist_m"])] for r in rows]


def test_compare_accepts_match_and_rejects_differences():
    assert cv.compare(cv.rows_to_result(cloud_rows(LOCAL)), LOCAL) == []
    assert cv.compare(cv.rows_to_result(cloud_rows(LOCAL))[1:], LOCAL)                        # missing pair
    assert cv.compare(cv.rows_to_result(cloud_rows(LOCAL) + [["H99", "S1", "10"]]), LOCAL)    # extra pair
    off = cloud_rows(LOCAL)
    off[0][2] = str(int(off[0][2]) + 1)
    assert cv.compare(cv.rows_to_result(off), LOCAL) == []                                     # 1 m rounding is fine
    off[0][2] = str(int(off[0][2]) + 5)
    assert cv.compare(cv.rows_to_result(off), LOCAL)


def test_sql_uses_st_dwithin_join():
    sql = cv.sql_statements(STORES, HOMES)["databricks"]
    assert "ON st_dwithin(h.geom, s.geom, 2500.0)" in sql
    assert "st_makepoint(x_m, y_m)" in sql and "{homes}" not in sql and "'H60'" in sql
    assert ";" not in sql
