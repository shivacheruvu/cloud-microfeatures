"""Day 6 Google Cloud: the local moving-average trend classifies the stores correctly, the SQL calls ML.TREND with a
horizon, and the cloud comparison accepts a matching BigQuery output and rejects a contradicting one (no cloud)."""
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-06-oat-milk-trend"
spec = importlib.util.spec_from_file_location("trend_day06", FEATURE / "trend.py")
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)


def fake_bq(slopes, horizon=14, status=""):
    """BigQuery-shaped rows: a straight trend per store with the given daily slope, plus forecast rows."""
    out = []
    for store, per_day in slopes.items():
        pts = tr.series(tr.load_rows())[store]
        start = date.fromisoformat(pts[0][0])
        for i in range(len(pts) + horizon):
            out.append({"store": store, "sale_date": (start + timedelta(days=i)).isoformat(),
                        "type": "history" if i < len(pts) else "forecast", "status": status,
                        "trend": 40 + per_day * i})
    return out


def test_local_classes():
    local = tr.run_local(tr.load_rows())
    assert local["north"]["class"] == "growing" and 3 < local["north"]["slope_per_week"] < 4
    assert local["south"]["class"] == "flat" and abs(local["south"]["slope_per_week"]) < 1


def test_moving_average_removes_the_weekday_pattern():
    # a pure weekly pattern with no trend has a flat 7-day moving average
    pts = [((date(2026, 6, 1) + timedelta(days=i)).isoformat(), [9, 8, 9, 10, 11, 13, 7][i % 7]) for i in range(56)]
    assert abs(tr.slope_per_week(tr.local_trend(pts))) < 1e-9


def test_sql_calls_ml_trend():
    sql = tr.bigquery_sql(tr.load_rows())
    assert "ML.TREND(" in sql and "horizon => 14" in sql and "id_cols => ['store']" in sql
    assert sql.count("DATE '") == len(tr.load_rows()) and not sql.lstrip().startswith("SELECT")


def test_compare_accepts_matching_and_rejects_contradicting_output():
    rows = tr.load_rows()
    errors, warnings, summary = tr.compare(fake_bq({"north": 0.49, "south": 0.03}), rows)
    assert errors == [] and warnings == []
    assert summary["stores"]["north"]["class"] == "growing" and summary["stores"]["north"]["forecast_rows"] == 14
    errors, _, _ = tr.compare(fake_bq({"north": 0.0, "south": 0.03}), rows)
    assert any("north" in e and "flat" in e for e in errors)
    errors, _, _ = tr.compare(fake_bq({"north": 0.49, "south": 0.03}, status="bad input"), rows)
    assert errors and errors[0].startswith("status")
    _, warnings, _ = tr.compare(fake_bq({"north": 0.49, "south": 0.03}, horizon=0), rows)
    assert any("forecast row" in w for w in warnings)


def test_read_bq_handles_warning_line_and_epoch(tmp_path):
    f = tmp_path / "out.json"
    f.write_text("WARNING: --scopes\n" + json.dumps([{"store": "north", "sale_date": "1780272000",
                                                      "time_series_type": "history", "trend": "41.5"}]))
    assert tr.read_bq(str(f)) == [{"store": "north", "sale_date": "2026-06-01", "type": "history",
                                   "status": "", "trend": 41.5}]


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    rows = tr.load_rows()
    summary = tr.summarise(tr.run_local(rows), rows)
    assert {k: result[k] for k in summary} == summary
