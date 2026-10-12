"""Day 7 Google Cloud: the committed data is the deterministic generator's output, the local counterfactual finds the
lift, the drop and the null, the SQL calls AI.CAUSAL_EFFECT, and the cloud comparison accepts a matching BigQuery
output and rejects a contradicting one (no cloud)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-07-endcap-effect"
spec = importlib.util.spec_from_file_location("causal_day07", FEATURE / "causal.py")
ce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ce)

ROWS = ce.load_rows()


def fake_bq(overrides=None, status=""):
    out = []
    for product, loc in ce.run_local(ROWS).items():
        r = {"product": product, "status": status, "p_value": loc["p_value"],
             "absolute_effect": loc["absolute_effect"], "relative_effect": loc["relative_effect"]}
        r.update((overrides or {}).get(product, {}))
        out.append(r)
    return out


def test_data_is_reproducible():
    assert [{**r, "units": int(r["units"])} for r in ce.make_data()] == ROWS
    assert len(ROWS) == 3 * (ce.PRE_DAYS + ce.POST_DAYS)


def test_local_classes():
    local = ce.run_local(ROWS)
    assert local["kombucha 16oz"]["class"] == "lift" and 0.3 < local["kombucha 16oz"]["relative_effect"] < 0.5
    assert local["oat milk 64oz"]["class"] == "drop" and -0.4 < local["oat milk 64oz"]["relative_effect"] < -0.2
    assert local["sparkling water 12pk"]["class"] == "no effect" and local["sparkling water 12pk"]["p_value"] > 0.5


def test_sql_calls_causal_effect():
    sql = ce.bigquery_sql(ROWS)
    assert "AI.CAUSAL_EFFECT(" in sql and "intervention_timestamp => '2026-09-01'" in sql
    assert "id_cols => ['product']" in sql and sql.count("DATE '") == len(ROWS)
    assert "{" not in sql and not sql.lstrip().startswith("SELECT")


def test_compare():
    errors, warnings, summary = ce.compare(fake_bq(), ROWS)
    assert errors == [] and warnings == [] and summary["kombucha 16oz"]["class"] == "lift"
    # BigQuery finds no significant effect for kombucha: an error
    errors, _, _ = ce.compare(fake_bq({"kombucha 16oz": {"p_value": 0.3}}), ROWS)
    assert any("kombucha" in e and "no effect" in e for e in errors)
    # same class, different size: a warning only
    errors, warnings, _ = ce.compare(fake_bq({"oat milk 64oz": {"relative_effect": -0.6}}), ROWS)
    assert errors == [] and any("oat milk" in w for w in warnings)
    errors, _, _ = ce.compare(fake_bq(status="too short"), ROWS)
    assert errors and errors[0].startswith("status")
    errors, _, _ = ce.compare(fake_bq()[:2], ROWS)
    assert any("no result row" in e for e in errors)


def test_read_bq_handles_warning_line(tmp_path):
    f = tmp_path / "out.json"
    f.write_text("WARNING: --scopes\n" + json.dumps([{"product": "kombucha 16oz", "p_value": "0.001",
                                                      "prob_causal_effect": "0.999", "absolute_effect": "340.5",
                                                      "relative_effect": "0.41", "status": ""}]))
    assert ce.read_bq(str(f)) == [{"product": "kombucha 16oz", "status": "", "p_value": 0.001,
                                   "absolute_effect": 340.5, "relative_effect": 0.41}]


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    summary = ce.summarise(ce.run_local(ROWS), ROWS)
    assert {k: result[k] for k in summary} == summary
