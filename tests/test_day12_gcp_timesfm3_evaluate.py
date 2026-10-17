"""Day 12 Google Cloud: the data is reproducible, the seasonal-naive baseline matches a hand check, the BigQuery
query calls AI.EVALUATE with TimesFM 3.0 and 2.5, and the cloud check accepts plausible bq JSON and rejects missing
rows, status messages and implausible errors (no cloud)."""
import importlib.util
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-12-forecast-accuracy"
spec = importlib.util.spec_from_file_location("evaluate_day12", FEATURE / "evaluate.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

ROWS = ev.load_rows()
LOCAL = ev.run_local(ROWS)


def test_data_is_reproducible_and_split():
    assert ev.make_data() == ROWS
    hist, actual = ev.split(ROWS)
    assert len(hist) == 3 * 112 and len(actual) == 3 * 14


def test_seasonal_naive_by_hand():
    hist, actual = ev.split(ROWS)
    item = "milk 1 gal"
    last = [r["units"] for r in hist if r["item"] == item][-7:]
    act = [r["units"] for r in actual if r["item"] == item]
    mae = sum(abs(a - last[i % 7]) for i, a in enumerate(act)) / 14
    assert abs(LOCAL[item]["mean_absolute_error"] - mae) < 1e-3
    assert {k: round(v["mean_absolute_percentage_error"], 2) for k, v in LOCAL.items()} == {
        "bananas (lb)": 5.01, "milk 1 gal": 3.04, "oat milk 64 oz": 2.84}


def bq_rows(scale=1.0, models=ev.MODELS):  # bq --format=json gives every value as a string
    return [{"model": m, "item": k, "mean_absolute_error": str(v["mean_absolute_error"] * scale),
             "root_mean_squared_error": str(v["root_mean_squared_error"] * scale),
             "mean_absolute_percentage_error": "4.2", "mean_absolute_scaled_error": "0.8", "ai_evaluate_status": ""}
            for m in models for k, v in LOCAL.items()]


def test_compare_accepts_plausible_output():
    errors, warnings, table = ev.compare(bq_rows(0.9), LOCAL)
    assert errors == [] and warnings == [] and set(table) == set(ev.ITEMS)
    errors, warnings, _ = ev.compare(bq_rows(1.5), LOCAL)
    assert errors == [] and len(warnings) == 3            # worse than naive is only a warning
    assert ev.compare(bq_rows(models=["TimesFM 2.5"]), LOCAL, ["TimesFM 2.5"])[0] == []


def test_compare_rejects_bad_output():
    assert ev.compare(bq_rows()[1:], LOCAL)[0]                          # missing row
    rows = bq_rows()
    rows[0]["ai_evaluate_status"] = "The time series data is too short."
    assert ev.compare(rows, LOCAL)[0]
    rows = bq_rows()
    rows[0]["mean_absolute_error"] = None
    assert ev.compare(rows, LOCAL)[0]
    assert ev.compare(bq_rows(10), LOCAL)[0]                            # implausibly large errors


def test_sql_calls_ai_evaluate_with_both_models():
    sql = ev.bigquery_sql(ROWS)
    assert sql.count("FROM AI.EVALUATE(") == 2 and "UNION ALL" in sql
    assert "model => 'TimesFM 3.0'" in sql and "model => 'TimesFM 2.5'" in sql and "horizon => 14" in sql
    assert "{history}" not in sql and "{actual}" not in sql and "DATE '2026-10-04'" in sql
    assert ev.bigquery_sql(ROWS, ["TimesFM 2.5"]).count("AI.EVALUATE(") == 1
