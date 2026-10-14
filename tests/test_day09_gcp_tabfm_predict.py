"""Day 9 Google Cloud: the committed data is the generator's output, the local least-squares model beats the naive
average, the SQL calls AI.PREDICT without leaking held-out labels, and the cloud check accepts good forecasts (with
or without the sku passed through) and rejects missing or naive ones (no cloud)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-09-new-item-forecast"
spec = importlib.util.spec_from_file_location("forecast_day09", FEATURE / "forecast.py")
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)

ROWS = fc.load_rows()
KNOWN, NEW = fc.split(ROWS)
LOCAL = fc.run_local(ROWS)


def test_data_is_reproducible():
    assert fc.make_data() == ROWS
    assert (len(KNOWN), len(NEW)) == (48, 8)


def test_local_model_beats_naive():
    assert LOCAL["mae_ols"] < 0.2 * LOCAL["mae_naive"]
    assert LOCAL["mae_ols"] == 17.3 and LOCAL["mae_naive"] == 214.0


def test_sql_sends_no_held_out_label():
    sql = fc.bigquery_sql(ROWS)
    assert "AI.PREDICT(" in sql and "label_col => 'weekly_units'" in sql and "{known}" not in sql
    new_part = sql.split("(SELECT * FROM UNNEST([", 2)[2]
    assert "weekly_units" not in new_part.split("]))")[0]
    assert all(f"'{r['sku']}' AS sku" in new_part for r in NEW)


def fake_bq(preds, with_sku=True):
    out = []
    for r in NEW:
        row = {f: str(r[f]).lower() if isinstance(r[f], bool) else str(r[f]) for f in fc.FEATURES}
        if with_sku:
            row["sku"] = r["sku"]
        row["predicted_weekly_units"] = None if preds.get(r["sku"]) is None else str(preds[r["sku"]])
        out.append(row)
    return out


def test_compare_accepts_good_forecasts():
    good = {r["sku"]: r["weekly_units"] * 1.1 for r in NEW}
    for with_sku in (True, False):
        errors, warnings, summary = fc.compare(fake_bq(good, with_sku), ROWS)
        assert errors == [] and len(summary["predictions"]) == 8


def test_compare_rejects_naive_and_missing():
    errors, _, _ = fc.compare(fake_bq(LOCAL["naive"]), ROWS)
    assert errors and "not better" in errors[0]
    partial = {r["sku"]: r["weekly_units"] for r in NEW[:7]}
    errors, _, _ = fc.compare(fake_bq(partial), ROWS)
    assert errors == [f"{NEW[7]['sku']}: no positive prediction (None)"]


def test_warns_when_far_behind_least_squares():
    rough = {r["sku"]: r["weekly_units"] * 1.6 for r in NEW}
    errors, warnings, _ = fc.compare(fake_bq(rough), ROWS)
    assert errors == [] and len(warnings) == 1
