"""Day 18 Databricks: pipeline.sql is up to date, reads exactly the two pipeline parameters, the local DuckDB run of
it matches a plain-Python model for the defaults and for the override, an unbound parameter fails like it does in a
pipeline, and the cloud comparison accepts a match and rejects a wrong store or changed defaults (no cloud)."""
import copy
import importlib.util
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-18-store-pipeline-parameters"
spec = importlib.util.spec_from_file_location("store_pipeline_day18", FEATURE / "store_pipeline.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

LOCAL = sp.run_local()


def test_pipeline_sql_is_rendered_from_scans():
    assert sp.pipeline_sql() == sp.render_pipeline_sql()


def test_parameters_and_defaults():
    assert sp.parameters_used(sp.pipeline_sql()) == ["start_date", "store_id"]
    assert set(sp.DEFAULTS) == {"start_date", "store_id"}            # every marker has a saved default
    assert sp.parameters_used("SELECT x::int, '10:12' AS t, :a FROM y -- :b") == ["a"]


def test_unbound_parameter_fails():
    with pytest.raises(KeyError):
        sp.bind(sp.pipeline_sql(), {"store_id": "S1"})


def test_local_matches_pure_python():
    for name, params, _ in sp.RUNS:
        assert LOCAL[name] == sp.pure_python(params)
    assert {r[0] for r in LOCAL["defaults"]} == {"S1"} and {r[0] for r in LOCAL["override"]} == {"S2"}
    assert min(r[1] for r in LOCAL["override"]) >= "2026-10-15"


def test_summary_numbers():
    s = sp.summarise(LOCAL)
    d, o = s["runs"]
    assert (d["days"], d["sales_cents"], d["units"]) == (7, 56908, 142)
    assert (o["days"], o["sales_cents"], o["units"]) == (4, 31134, 86)
    assert o["parameters_sent"] == sp.OVERRIDE and d["parameters_sent"] == {}
    assert s["scan_lines_in_source"] == 94


def test_compare():
    cloud = {"tables": {k: [list(r) for r in v] for k, v in LOCAL.items()}, "saved_defaults_after": dict(sp.DEFAULTS)}
    assert sp.compare(cloud, LOCAL) == []
    wrong = copy.deepcopy(cloud)
    wrong["tables"]["override"] = [list(r) for r in LOCAL["defaults"]]   # override ignored: defaults used again
    assert sp.compare(wrong, LOCAL)
    changed = copy.deepcopy(cloud)
    changed["saved_defaults_after"] = {"store_id": "S2", "start_date": "2026-10-15"}
    assert any("saved defaults" in e for e in sp.compare(changed, LOCAL))
