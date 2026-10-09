"""Day 4 Google Cloud: the local detector finds the seeded shelf move, and the BigQuery-output checker accepts a
correct ML.DETECT_CHANGE_POINTS result and rejects wrong ones (no cloud access needed)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-04-change-points"
spec = importlib.util.spec_from_file_location("change_points", FEATURE / "change_points.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)
ROWS = cp.load_rows()


def bq_row(store, begin, end, status=""):
    m = cp.window_metrics(cp.series(ROWS)[store], begin, end)
    return {"store": store, "begin_timestamp": f"{begin} 00:00:00", "end_timestamp": f"{end} 00:00:00",
            "metrics": {"avg": m["avg"], "min": m["min"], "max": m["max"], "stddev": m["stddev_samp"],
                        "count": str(m["count"])}, "status": status}


def check(tmp_path, rows):
    f = tmp_path / "bq.json"
    f.write_text("WARNING: --scopes something\n" + json.dumps(rows))
    return cp.compare(cp.read_bq(str(f)), ROWS)


def test_local_detector_finds_the_shelf_move():
    local = cp.run_local(ROWS)
    assert local["east"]["change_date"] == "2026-07-10"
    assert 50 < local["east"]["shift_pct"] < 60
    assert local["west"] is None


def test_detector_ignores_a_single_day_spike():
    pts = [(f"2026-06-{d:02d}", 30) for d in range(1, 31)]
    pts[14] = (pts[14][0], 90)
    assert cp.detect(pts) is None


def test_compare_accepts_a_window_around_the_change(tmp_path):
    errors, warnings, summary = check(tmp_path, [bq_row("east", "2026-07-02", "2026-07-18")])
    assert errors == [] and warnings == [] and summary["metrics_matched"] == 1


def test_compare_rejects_a_window_elsewhere_and_bad_status(tmp_path):
    errors, _, _ = check(tmp_path, [bq_row("east", "2026-06-02", "2026-06-18")])
    assert errors and "2026-07-10" in errors[0]
    errors, _, _ = check(tmp_path, [bq_row("east", "2026-07-02", "2026-07-18", status="bad input")])
    assert errors


def test_compare_warns_on_wrong_metrics_and_flat_series_windows(tmp_path):
    bad = bq_row("east", "2026-07-02", "2026-07-18")
    bad["metrics"]["avg"] += 1
    errors, warnings, _ = check(tmp_path, [bad, bq_row("west", "2026-07-01", "2026-07-10")])
    assert errors == [] and len(warnings) == 2


def test_sql_inlines_every_row_and_uses_id_cols():
    sql = cp.bigquery_sql(ROWS)
    assert sql.count("DATE '2026-") == len(ROWS) == 182
    assert "ML.DETECT_CHANGE_POINTS(" in sql and "id_cols => ['store']" in sql
    assert "data_col => 'units'" in sql and "timestamp_col => 'sale_date'" in sql


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    assert result["shifts"] == cp.run_local(ROWS)
