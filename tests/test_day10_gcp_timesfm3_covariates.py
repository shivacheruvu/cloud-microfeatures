"""Day 10 Google Cloud: the committed data is the generator's output, the promo-aware baseline beats seasonal naive,
the SQL sends the promo calendar but never the held-out sales, and the cloud check reads both AI.FORECAST output
shapes, accepts a good forecast and rejects missing or naive ones (no cloud)."""
import importlib.util
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-10-promo-aware-forecast"
spec = importlib.util.spec_from_file_location("forecast_day10", FEATURE / "forecast.py")
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)

ROWS = fc.load_rows()
HIST, HOLD = fc.split(ROWS)
LOCAL = fc.run_local(ROWS)


def test_data_is_reproducible():
    assert fc.make_data() == ROWS
    assert (len(HIST), len(HOLD)) == (84, 14)
    assert sum(r["on_promo"] for r in HOLD) == 3


def test_promo_aware_baseline_beats_naive():
    assert LOCAL["mae_model"] < 0.5 * LOCAL["mae_naive"]
    assert LOCAL["mae_model_promo_days"] < 0.25 * LOCAL["mae_naive_promo_days"]
    assert 1.4 < LOCAL["lift"] < 1.8


def test_sql_sends_promos_not_held_out_sales():
    sql = fc.bigquery_sql(ROWS)
    assert "model => 'TimesFM 3.0'" in sql and "future_covariate_cols => ['on_promo']" in sql
    assert "horizon => 14" in sql and "{sales}" not in sql
    for r in HOLD:
        assert f"DATE '{r['day']}' AS day, CAST(NULL AS FLOAT64) AS units, {float(r['on_promo'])} AS on_promo" in sql
        assert f" {r['units']} AS units" not in sql or any(h["units"] == r["units"] for h in HIST)
    plain = fc.bigquery_sql(ROWS, with_promos=False)
    assert "future_covariate_cols" not in plain and HOLD[0]["day"] not in plain


def multivariate(values):
    return [{"day": r["day"], "units": {"value": None if values.get(r["day"]) is None else str(values[r["day"]]),
                                        "prediction_interval_lower_bound": "0", "prediction_interval_upper_bound": "1"},
             "confidence_level": "0.9", "ai_forecast_status": ""} for r in HOLD]


def univariate(values):
    return [{"forecast_timestamp": r["day"] + " 00:00:00 UTC", "forecast_value": str(values[r["day"]])} for r in HOLD]


def test_compare_accepts_good_forecast_in_either_shape():
    good = {r["day"]: r["units"] * 1.03 for r in HOLD}
    flat = {r["day"]: sum(h["units"] for h in HIST[-7:]) / 7 for r in HOLD}
    errors, warnings, summary = fc.compare(multivariate(good), ROWS, univariate(flat))
    assert errors == [] and warnings == []
    assert summary["mae_timesfm_promos"] < summary["mae_timesfm_plain"]
    errors, _, _ = fc.compare(univariate(good), ROWS)
    assert errors == []


def test_compare_rejects_missing_and_naive():
    partial = {r["day"]: r["units"] for r in HOLD[:-1]}
    errors, _, _ = fc.compare(multivariate(partial), ROWS)
    assert len(errors) == 1 and HOLD[-1]["day"] in errors[0]
    errors, _, _ = fc.compare(multivariate(LOCAL["naive"]), ROWS)
    assert errors and "seasonal naive" in errors[0]


def test_compare_warns_when_covariate_does_not_help():
    good = {r["day"]: r["units"] * 1.03 for r in HOLD}
    _, warnings, _ = fc.compare(multivariate(good), ROWS, univariate(good))
    assert any("didn't help" in w for w in warnings)
