"""Day 33 Google Cloud: the DuckDB recursive-CTE twin of the GQL ANY SHORTEST query equals the plain-Python BFS, every
fewest-hop path is unique (so ANY SHORTEST is deterministic), the transfer plan is right, the generated GQL uses ANY
SHORTEST with a LET over the edge group, and the cloud comparison accepts a match and rejects a wrong route (no cloud)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-33-stock-transfer-routes"
spec = importlib.util.spec_from_file_location("transfer_routes_day33", FEATURE / "transfer_routes.py")
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)

LOCAL = tr.run_local()
RESULT = json.loads((FEATURE / "result.json").read_text())


def test_local_matches_pure_python():
    assert LOCAL == tr.pure_python()
    assert tr.compare(LOCAL, tr.pure_python()) == []


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(tr.summarise(LOCAL)))


def test_plan():
    p = tr.plan(LOCAL)
    assert p["S3"] == {"from": "DC", "hops": 2, "minutes": 22}
    assert p["S5"] == {"from": "S2", "hops": 2, "minutes": 16}      # DC is 3 hops away
    assert p["S7"] == {"from": "S2", "hops": 3, "minutes": 33}      # DC is 4 hops: out of reach
    assert p["S8"] is None
    s = tr.summarise(LOCAL)
    assert (s["pairs_within_3_hops"], s["cases_routed"], s["drive_minutes"]) == (7, 25, 91)


def test_ambiguous_shortest_paths_are_refused(monkeypatch):
    monkeypatch.setattr(tr, "ROADS", tr.ROADS + [("S3", "S5", 15)])   # DC->S5 gets two 3-hop paths
    with pytest.raises(ValueError, match="ambiguous"):
        tr.pure_python()


def test_generated_sql():
    gql = tr.bigquery_sql("routes", "p.d")
    assert "MATCH ANY SHORTEST" in gql and "->{1, 3}" in gql and "LET minutes = SUM(e.minutes)" in gql
    assert "ARRAY_LENGTH(e) AS hops" in gql and not gql.lstrip().startswith("--")
    setup = tr.bigquery_sql("setup", "p.d")
    assert "CREATE OR REPLACE PROPERTY GRAPH `p.d`.store_routes" in setup and setup.count("('S4', 'S5', 7)") == 1


def test_cloud_compare(tmp_path, capsys):
    rows = [{"src": s, "dst": d, "hops": str(h), "minutes": str(m)} for s, d, h, m in LOCAL]
    f = tmp_path / "routes.json"
    f.write_text("WARNING: --scopes ignored\n" + json.dumps(rows))
    assert tr.main(["--compare", str(f)]) == 0
    assert "matched the local model (7 surplus-to-short routes)" in capsys.readouterr().out
    rows[0]["minutes"] = "99"
    f.write_text(json.dumps(rows))
    assert tr.main(["--compare", str(f)]) == 1
    assert "::error" in capsys.readouterr().out
