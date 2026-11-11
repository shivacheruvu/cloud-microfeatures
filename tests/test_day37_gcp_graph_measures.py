"""Day 37 Google Cloud: the DuckDB twin (aggregating at each key's grain) equals the plain-Python model, naive SUM over
the flattened graph overcounts as expected, the generated DDL declares MEASURE properties and the query reads them with
AGG over GRAPH_EXPAND, and the cloud comparison accepts a match and rejects a wrong number (no cloud)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-37-flyer-reach-measures"
spec = importlib.util.spec_from_file_location("flyer_measures_day37", FEATURE / "flyer_measures.py")
fm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fm)

LOCAL = fm.run_local()
RESULT = json.loads((FEATURE / "result.json").read_text())


def test_local_matches_pure_python():
    assert LOCAL == fm.pure_python()


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(fm.summarise(LOCAL)))


def test_measures_count_each_key_once():
    rows = {r[0]: dict(zip(fm.COLS, r)) for r in LOCAL}
    gv = rows["Green Valley Dairy-Free"]
    assert gv["units"] == 420 + 260 + 150 == 830
    assert gv["naive_units"] == 420 * 3 + 260 + 150 * 2 == 1820          # oat milk on 3 flyers, yogurt on 2
    assert gv["households"] == 48000 + 21000 + 9000                    # F1, F2, F3 once each
    assert sum(r["units"] for r in rows.values()) == sum(p[3] for p in fm.PRODUCTS) == 2470
    assert sum(r["slots"] for r in rows.values()) == len(fm.SLOTS)
    s = fm.summarise(LOCAL)
    assert (s["units_naive"], s["units_overcount_pct"], s["worst_brand_factor"]) == (4680, 89.5, 2.19)


def test_every_product_is_on_a_flyer():
    assert {p for _, _, p in fm.SLOTS} == {p[0] for p in fm.PRODUCTS}


def test_generated_sql():
    setup = fm.bigquery_sql("setup", "p.d")
    assert "CREATE OR REPLACE PROPERTY GRAPH `p.d`.FlyerGraph" in setup
    assert "MEASURE(SUM(weekly_units)) AS total_units" in setup and "MEASURE(SUM(households)) AS total_households" in setup
    assert setup.count("NO PROPERTIES") == 3 and "('S01', 'F1', 'P1')" in setup
    q = fm.bigquery_sql("expand", "p.d")
    assert 'GRAPH_EXPAND("p.d.FlyerGraph")' in q and "AGG(Products_total_units)" in q
    assert "SUM(Products_weekly_units)" in q and not q.lstrip().startswith("--")
    assert 'GRAPH_EXPAND("d.FlyerGraph")' in fm.bigquery_sql("expand", "p.d", "d.FlyerGraph")
    assert "SUM(Products_total_units)" in fm.bigquery_sql("guard", "p.d")


def test_cloud_compare(tmp_path, capsys):
    rows = [{c: (str(v) if i else v) for i, (c, v) in enumerate(zip(fm.COLS, r))} for r in LOCAL]
    f = tmp_path / "expand.json"
    f.write_text("WARNING: --scopes ignored\n" + json.dumps(rows))
    assert fm.main(["--compare", str(f)]) == 0
    assert "matched the local model (3 brands)" in capsys.readouterr().out
    rows[0]["units"] = "1820"
    f.write_text(json.dumps(rows))
    assert fm.main(["--compare", str(f)]) == 1
    assert "::error" in capsys.readouterr().out
