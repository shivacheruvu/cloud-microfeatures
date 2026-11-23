"""Day 48 Databricks: the deterministic Thanksgiving generator, local baselines, the ai_forecast v2 SQL for each
variant, and the structural check of Statement Execution API rows (row count, interval order, positive_only)."""
import importlib.util
import json
from datetime import date
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-48-thanksgiving-forecast"
spec = importlib.util.spec_from_file_location("forecast48", FEATURE / "forecast.py")
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)


def test_generator():
    h = fc.history()
    assert len(h) == 2325 and h[0][0] == "2024-10-01" and max(r[0] for r in h) == "2026-11-14"
    assert all(u >= 0 for _, _, u, _ in h)
    # the turkey spike sits on the day before Thanksgiving in both history years
    for tg in ("2024-11-27", "2025-11-26"):
        turkey = {d: u for d, s, u, _ in h if s == "TURKEY-WHOLE"}
        assert turkey[tg] == max(turkey[d] for d in turkey if d[:4] == tg[:4])
    assert not any(p for d, _, _, p in h if "2025-11-13" <= d <= "2025-12-10")   # no promos near Thanksgiving
    assert fc.promo_on(date(2026, 11, 22)) and not fc.promo_on(date(2026, 11, 27))
    assert len(fc.actuals()) == 48 and len(fc.future_dates()) == 16


def test_result_json_and_baselines():
    s = fc.summarise()
    saved = json.loads((FEATURE / "result.json").read_text())
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))
    b = s["baselines"]
    assert b["last_thanksgiving"]["wape"] < b["last_week"]["wape"]   # the holiday matters
    assert b["actual_turkey_peak"] > 10 * fc.SKUS["TURKEY-WHOLE"][0]


def test_sql_variants():
    plain, hol, promo = (fc.sql(v) for v in fc.VARIANTS)
    for q in (plain, hol, promo):
        assert q.startswith("--") and "version => '2'" in q and "horizon => '2026-12-01'" in q
        assert "group_col => 'sku'" in q and "TABLE(daily)" in q
    assert "holiday_region" not in plain and "positive_only" not in plain
    assert "holiday_region => 'US'" in hol and "positive_only => true" in hol and "covariate_col" not in hol
    assert "covariate_col => 'promo'" in promo
    assert plain.count("(DATE'") == 2325 and promo.count("(DATE'") == 2325 + 48 and promo.count(", NULL, ") == 48


def _rows(variant, change=None, drop=0):
    rows = [[variant, d + "T00:00:00", s, str(u), str(max(0, u - 3)), str(u + 3)] for (d, s), u in fc.actuals().items()]
    for i, r in (change or {}).items():
        rows[i] = r
    return rows[drop:]


def test_check_and_score():
    got = fc.parse(_rows("holiday"))
    assert fc.check("holiday", got) == []
    s = fc.score(got)
    assert s["wape"] == 0 and s["turkey_peak_forecast"] == 80 and s["negative_forecasts"] == 0
    assert fc.check("holiday", fc.parse(_rows("holiday", drop=1)))
    neg = ["holiday", "2026-11-30", "TURKEY-WHOLE", "-0.5", "-2", "1"]
    assert any("positive_only" in p for p in fc.check("holiday", fc.parse(_rows("holiday", {47: neg}))))
    flipped = ["plain", "2026-11-30", "TURKEY-WHOLE", "5", "6", "4"]
    assert any("out of order" in p for p in fc.check("plain", fc.parse(_rows("plain", {47: flipped}))))


def test_refusal_detection_and_redaction(monkeypatch):
    assert fc.compute_refused("GET /api/2.0/sql/warehouses 403: Organization 123 has been cancelled or is not active")
    assert fc.compute_refused("FEATURE_DISABLED: serverless")
    assert not fc.compute_refused("SQLSTATE 42703 column not found")
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-abc.cloud.databricks.com")
    assert fc._redact("dbc-abc.cloud.databricks.com me@x.com 12345678") == "<host> <user> <id>"
