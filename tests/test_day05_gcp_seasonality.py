"""Day 5 Google Cloud: the local weekly decomposition recovers the seeded weekday pattern, the query is well formed,
and --compare accepts a BigQuery-shaped output that agrees and rejects one with the wrong peak day."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-05-weekly-seasonality"
spec = importlib.util.spec_from_file_location("seasonality", FEATURE / "seasonality.py")
sz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sz)


def fake_bq(rows, shift_peak_to=None):
    """BigQuery-shaped rows whose weekly component is the local profile (optionally moving north's peak)."""
    local = sz.run_local(rows)
    out = []
    for r in rows:
        prof = dict(local[r["store"]]["profile"])
        if shift_peak_to and r["store"] == "north":
            prof[shift_peak_to] = max(prof.values()) + 10
        out.append({"store": r["store"], "sale_date": f"{r['sale_date']} 00:00:00 UTC", "time_series_type": "history",
                    "units": r["units"], "weekly": prof[sz.weekday(r["sale_date"])], "status": ""})
    return out


def test_local_profile_recovers_seeded_weekdays():
    local = sz.run_local(sz.load_rows())
    assert (local["north"]["peak_day"], local["north"]["trough_day"]) == ("Sat", "Tue")
    assert local["south"]["peak_day"] == "Fri"
    for store in local.values():
        assert abs(sum(store["profile"].values())) < 0.5  # centred
    assert 50 < local["north"]["peak_vs_avg"] < 65 and 30 < local["south"]["peak_vs_avg"] < 45


def test_sql_is_one_call_with_weekly_only():
    rows = sz.load_rows()
    sql = sz.bigquery_sql(rows)
    assert sql.count("ML.SEASONALITY(") == 1 and "seasonalities => ['WEEKLY']" in sql
    assert "id_cols => ['store']" in sql and sql.count("(DATE '") == len(rows)
    assert "--max_rows=1000" in (FEATURE / "cloud.sh").read_text() and len(rows) > 100


def test_compare_accepts_agreeing_output(tmp_path):
    rows = sz.load_rows()
    f = tmp_path / "bq.json"
    f.write_text("WARNING: scopes\n" + json.dumps(fake_bq(rows)))
    errors, warnings, summary = sz.compare(sz.read_bq(f), rows)
    assert errors == [] and warnings == []
    assert summary["stores"]["north"]["peak_day"] == "Sat" and summary["stores"]["north"]["correlation"] == 1.0


def test_compare_rejects_wrong_peak_and_status(tmp_path):
    rows = sz.load_rows()
    bad = fake_bq(rows, shift_peak_to="Mon")
    bad[0]["status"] = "invalid input"
    f = tmp_path / "bq.json"
    f.write_text(json.dumps(bad))
    errors, _, _ = sz.compare(sz.read_bq(f), rows)
    assert any("status" in e for e in errors) and any("peak weekday Mon != local Sat" in e for e in errors)


def test_compare_rejects_missing_weekly(tmp_path):
    rows = sz.load_rows()
    out = [dict(r, weekly=None) if r["store"] == "south" else r for r in fake_bq(rows)]
    f = tmp_path / "bq.json"
    f.write_text(json.dumps(out))
    errors, _, _ = sz.compare(sz.read_bq(f), rows)
    assert errors and errors[0].startswith("south: no weekly component")


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    rows = sz.load_rows()
    summary = sz.summarise(sz.run_local(rows), rows)
    assert {k: result[k] for k in summary} == summary
