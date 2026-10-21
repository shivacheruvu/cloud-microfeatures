"""Day 16 Databricks: the committed data is the generator's output and keeps every home 5 m clear of every zone edge,
the local DuckDB rows match plain Python (reversed corners normalised, a collapsed zone contains nothing), the SQL
builds boxes with st_makeenvelope, and the cloud comparison accepts Statement-Execution-shaped rows that match and
rejects differences (no cloud)."""
import importlib.util
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-16-flyer-zones"
spec = importlib.util.spec_from_file_location("zones_day16", FEATURE / "zones.py")
zn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zn)

ZONES, HOMES = zn.load()
LOCAL = zn.run_local(ZONES, HOMES)


def test_data_is_reproducible_and_clear_of_edges():
    assert zn.make_data() == (ZONES, HOMES)
    assert all(zn._clear_of_edges(h["x_m"], h["y_m"]) for h in HOMES)


def test_local_matches_pure_python():
    assert zn.compare(LOCAL, zn.pure_python(ZONES, HOMES)) == []
    assert zn.compare(zn.pure_python(ZONES, HOMES), LOCAL) == []


def test_reversed_corners_equal_normal_corners():
    z2 = next(z for z in ZONES if z["zone_id"] == "Z2")
    flipped = {**z2, "x1": z2["x2"], "y1": z2["y2"], "x2": z2["x1"], "y2": z2["y1"]}
    assert zn.compare(zn.pure_python([flipped], HOMES), zn.pure_python([z2], HOMES)) == []


def test_summary_numbers():
    s = zn.summarise(LOCAL, ZONES, HOMES)
    assert (s["flyers"], s["homes_reached"], s["homes_with_two_flyers"]) == (34, 31, 3)
    assert s["bad_zones"] == ["Z5"] and s["reversed_corner_zones"] == ["Z2"]
    z5 = s["table"][-1]
    assert z5["shape"] == "line" and z5["homes"] == 0 and z5["area_km2"] == 0
    assert sum(t["homes"] for t in s["table"]) == s["flyers"]


def cloud_rows(rows):
    """What the Statement Execution API returns: strings, and null for the unmatched LEFT JOIN side."""
    return [[r["zone_id"], str(r["npoints"]), str(r["area_m2"]), r["home_id"]] for r in rows]


def test_compare_accepts_match_and_rejects_differences():
    good = cloud_rows(LOCAL)
    assert zn.compare(zn.rows_to_result(good), LOCAL) == []
    assert zn.compare(zn.rows_to_result([r for r in good if r[0] != "Z1"]), LOCAL)        # zone missing
    assert zn.compare(zn.rows_to_result(good + [["Z1", "5", good[0][2], "H99"]]), LOCAL)  # extra home
    bad = [list(r) for r in good]
    for r in bad:
        if r[0] == "Z5":
            r[1] = "5"                                                                     # line read as a box
    assert zn.compare(zn.rows_to_result(bad), LOCAL)


def test_sql_uses_st_makeenvelope():
    sql = zn.sql_statements(ZONES, HOMES)["databricks"]
    assert "st_makeenvelope(x1, y1, x2, y2)" in sql and "st_contains(z.box, h.geom)" in sql
    assert "('Z2', 'Riverside', 8800.0D, 8800.0D, 5200.0D, 5600.0D)" in sql
    assert "{homes}" not in sql and "'H80'" in sql and ";" not in sql
