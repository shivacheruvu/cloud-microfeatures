"""Day 11 Google Cloud: the committed data is the generator's output, the local baseline finds the planted problems,
the SQL sends history and target separately with TimesFM 3.0, and the cloud check reads AI.DETECT_ANOMALIES rows
(bq JSON strings, TIMESTAMP as text or epoch), accepts a good result and rejects misses or bad rows (no cloud)."""
import importlib.util
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-11-scan-anomalies"
spec = importlib.util.spec_from_file_location("anomalies_day11", FEATURE / "anomalies.py")
an = importlib.util.module_from_spec(spec)
spec.loader.exec_module(an)

ROWS = an.load_rows()
HIST, TARGET = an.split(ROWS)
LOCAL = an.run_local(ROWS)


def test_data_is_reproducible():
    assert an.make_data() == ROWS
    assert (len(HIST), len(TARGET)) == (3 * 56, 3 * 28)
    assert [r["planted"] for r in TARGET if r["planted"]] == ["scanner outage", "duplicate scans", "short delivery"]
    assert not any(r["planted"] for r in HIST)


def test_local_baseline_catches_planted_without_false_alarms():
    s = an.score({k: v["is_anomaly"] for k, v in LOCAL.items()}, ROWS)
    assert len(s["caught"]) == 3 and s["false_alarms"] == []


def test_sql():
    sql = an.bigquery_sql(ROWS)
    assert "AI.DETECT_ANOMALIES(" in sql and "model => 'TimesFM 3.0'" in sql and "id_cols => ['product']" in sql
    assert "{history}" not in sql and "{target}" not in sql
    assert sql.count("STRUCT(") == len(ROWS)
    assert "model => 'TimesFM 2.5'" in an.bigquery_sql(ROWS, "TimesFM 2.5")


def fake_cloud(flagged, epoch=False, status=""):
    out = []
    for r in TARGET:
        ts = an.datetime.fromisoformat(r["day"] + "T00:00:00+00:00")
        out.append({"product": r["product"],
                    "time_series_timestamp": str(ts.timestamp()) if epoch else f"{r['day']} 00:00:00",
                    "time_series_data": str(float(r["units"])), "is_anomaly": "true" if (r["product"], r["day"]) in flagged else "false",
                    "lower_bound": "50.0", "upper_bound": "250.0", "anomaly_probability": "0.5",
                    "ai_detect_anomalies_status": status})
    return out


PLANTED = {(r["product"], r["day"]) for r in TARGET if r["planted"]}


def test_compare_accepts_good_results():
    for epoch in (False, True):
        errors, warnings, summary = an.compare(fake_cloud(PLANTED, epoch), ROWS)
        assert errors == [] and warnings == [] and summary["caught"] == 3
    one_missed = set(list(PLANTED)[:2])
    errors, warnings, _ = an.compare(fake_cloud(one_missed), ROWS)
    assert errors == [] and len(warnings) == 1


def test_compare_rejects_misses_and_bad_rows():
    errors, _, _ = an.compare(fake_cloud(set(list(PLANTED)[:1])), ROWS)
    assert errors and "only 1 of 3" in errors[0]
    assert an.compare(fake_cloud(PLANTED)[1:], ROWS)[0]                       # a missing point
    assert an.compare(fake_cloud(PLANTED, status="too short"), ROWS)[0]       # a status message
    noisy = PLANTED | {(r["product"], r["day"]) for r in TARGET[:10]}
    errors, warnings, _ = an.compare(fake_cloud(noisy), ROWS)
    assert errors == [] and any("normal days flagged" in w for w in warnings)
