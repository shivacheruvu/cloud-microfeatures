"""Day 2 Databricks: the counter_diff query (run locally on DuckDB) matches a plain-Python counter walk."""
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

import duckdb

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-02-lane-counters"
spec = importlib.util.spec_from_file_location("lane_counters", FEATURE / "lane_counters.py")
lc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lc)


def python_reference(rows):
    by_lane = defaultdict(list)
    for r in rows:
        by_lane[r["lane"]].append((r["read_at"], int(r["scan_counter"])))
    out = []
    for lane, readings in sorted(by_lane.items()):
        readings.sort()
        counters = [c for _, c in readings]
        deltas = [None] + [b - a if b >= a else None for a, b in zip(counters, counters[1:])]
        known = [d for d in deltas if d is not None]
        out.append({"lane": lane, "readings": len(readings), "items_scanned": sum(known),
                    "resets": deltas.count(None) - 1, "peak_items_15min": max(known),
                    "naive_max_minus_min": max(counters) - min(counters)})
    return out


def test_counter_diff_query_matches_reference():
    rows = lc.load_rows()
    assert lc.run_local(lc.build_query(rows)) == python_reference(rows)


def test_translation_matches_documented_examples():
    """Examples 1 and 2 from the counter_diff docs: NULL first, deltas, NULL on reset."""
    sql = lc.to_duckdb("""SELECT counter_diff(c) OVER (PARTITION BY m ORDER BY t) AS d FROM (VALUES
        ('h', TIMESTAMP '2026-01-01 00:00:00', 100), ('h', TIMESTAMP '2026-01-01 00:01:00', 200),
        ('h', TIMESTAMP '2026-01-01 00:02:00', 400), ('h', TIMESTAMP '2026-01-01 00:04:00', 50)) AS tab(m, t, c)
        ORDER BY t""")
    assert [r[0] for r in duckdb.sql(sql).fetchall()] == [None, 100, 200, None]


def test_resets_are_handled_not_summed():
    lanes = {r["lane"]: r for r in lc.run_local(lc.build_query(lc.load_rows()))}
    assert lanes["lane-4"]["resets"] == 2 and lanes["lane-1"]["resets"] == 0
    assert lanes["lane-1"]["items_scanned"] == lanes["lane-1"]["naive_max_minus_min"]
    assert all(r["items_scanned"] > 0 for r in lanes.values())


def test_query_uses_counter_diff():
    sql = (FEATURE / "lane_counters.sql").read_text()
    assert "counter_diff(scan_counter) OVER (PARTITION BY lane ORDER BY read_at)" in sql


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    assert result["by_lane"] == lc.run_local(lc.build_query(lc.load_rows()))
