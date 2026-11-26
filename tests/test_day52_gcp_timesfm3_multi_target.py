"""Day 52 Google Cloud: the committed data is the generator's output, the SQL asks TimesFM 3.0 for three targets in
one call and never sends the held-out sales, and the cloud check reads the wide multi-target output, accepts a good
forecast and rejects missing, broken or badly banded ones (no cloud)."""
import importlib.util
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-52-baking-aisle-multi-target"
spec = importlib.util.spec_from_file_location("forecast_day52", FEATURE / "forecast.py")
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)

ROWS = fc.load_rows()
HIST, HOLD = fc.split(ROWS)
ACT = fc.actuals(ROWS)
LOCAL = fc.run_local(ROWS)


def test_data_is_reproducible():
    assert fc.make_data() == ROWS
    assert (len(HIST), len(HOLD), len(ACT)) == (224, 28, 84)
    assert len(HIST) // 2 >= 64  # TimesFM needs at least 64 points of context


def test_local_baselines():
    assert 3 < LOCAL["wape_mean4"] < LOCAL["wape_naive"] < 8
    assert set(LOCAL["wape_naive_by_item"]) == {"flour", "butter", "eggs"}


def test_sql_is_multi_target_and_hides_held_out_sales():
    sql = fc.bigquery_sql(ROWS)
    assert "target_cols => ['flour', 'butter', 'eggs']" in sql and "model => 'TimesFM 3.0'" in sql
    assert "id_cols => ['store']" in sql and "horizon => 14" in sql and "data_col" not in sql
    assert "{sales}" not in sql and sql.count("STRUCT('") == len(HIST)
    for r in HOLD:
        assert f"DATE '{r['day']}'" not in sql
    uni = fc.bigquery_sql(ROWS, univariate=True)
    assert "data_col => 'units'" in uni and "id_cols => ['store', 'item']" in uni and "target_cols" not in uni
    assert "UNPIVOT" in uni and HOLD[0]["day"] not in uni


def wide(values, spread=0.08, status=""):
    rows = {}
    for (store, item, day), v in values.items():
        r = rows.setdefault((store, day), {"store": store, "day": day, "ai_forecast_status": status})
        r[item] = None if v is None else str(v)
        if v is not None:
            r[f"{item}_lo"], r[f"{item}_hi"] = str(v * (1 - spread)), str(v * (1 + spread))
    return list(rows.values())


def long(values):
    return [{"store": s, "item": i, "day": d, "value": str(v)} for (s, i, d), v in values.items()]


def test_compare_accepts_good_forecast():
    good = {k: v * 1.02 for k, v in ACT.items()}
    errors, warnings, summary = fc.compare(wide(good), ROWS, long(LOCAL["naive"]))
    assert errors == [] and warnings == []
    assert summary["forecasts"] == 84 and summary["wape_multi"] < 2.5 and summary["coverage_90"] == 1.0
    assert summary["wape_univariate_2_5"] == LOCAL["wape_naive"]


def test_compare_rejects_missing_status_and_broken():
    partial = dict(ACT)
    partial[next(iter(ACT))] = None
    errors, _, _ = fc.compare(wide(partial), ROWS)
    assert errors and "1 of 84" in errors[-1]
    errors, _, _ = fc.compare(wide(ACT, status="series too short"), ROWS)
    assert errors and "ai_forecast_status" in errors[0]
    broken = {k: v * 1.3 for k, v in ACT.items()}
    errors, _, _ = fc.compare(wide(broken), ROWS)
    assert errors and "seasonal naive" in errors[0]


def test_compare_flags_bad_intervals_and_naive_level_forecast():
    inverted = wide(ACT, spread=-0.05)  # lo above the forecast, hi below it
    errors, _, _ = fc.compare(inverted, ROWS)
    assert errors and "intervals" in errors[0]
    errors, warnings, _ = fc.compare(wide(LOCAL["naive"], spread=0.001), ROWS)
    assert errors == []
    assert any("not better than seasonal naive" in w for w in warnings)
    assert any("90% intervals" in w for w in warnings)
