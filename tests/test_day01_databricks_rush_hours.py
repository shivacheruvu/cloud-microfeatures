"""Day 1 Databricks: the time_bucket query (run locally on DuckDB) matches a plain-Python bucketing."""
import importlib.util
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-01-scan-rush-hours"
spec = importlib.util.spec_from_file_location("rush_hours", FEATURE / "rush_hours.py")
rh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rh)


def sql_round(x):
    """SQL ROUND(x, 1) rounds halves away from zero; Python's round() does not."""
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def python_reference(rows):
    slot = timedelta(minutes=rh.SLOT_MINUTES)
    origin = datetime(1970, 1, 1, 0, rh.ORIGIN_MINUTE)
    groups = defaultdict(list)
    for r in rows:
        ts = datetime.fromisoformat(r["scanned_at"])
        groups[origin + ((ts - origin) // slot) * slot].append(int(r["health_score"]))
    return [{"slot_start": k.strftime("%Y-%m-%d %H:%M"), "scans": len(v),
             "avg_health_score": sql_round(sum(v) / len(v)),
             "pct_healthy": sql_round(100 * sum(s >= rh.HEALTHY for s in v) / len(v))} for k, v in sorted(groups.items())]


def test_time_bucket_query_matches_reference():
    rows = rh.load_rows()
    assert rh.run_local(rh.build_query(rows)) == python_reference(rows)


def test_slots_align_to_store_opening():
    slots = rh.run_local(rh.build_query(rh.load_rows()))
    assert all(s["slot_start"][14:16] in ("15", "45") for s in slots)


def test_query_uses_time_bucket_with_origin():
    sql = (FEATURE / "rush_hours.sql").read_text()
    assert "time_bucket(INTERVAL '30' MINUTE, scanned_at, TIMESTAMP '1970-01-01 00:15:00')" in sql


def test_result_json_is_current():
    import json
    result = json.loads((FEATURE / "result.json").read_text())
    assert result["by_slot"] == rh.run_local(rh.build_query(rh.load_rows()))
