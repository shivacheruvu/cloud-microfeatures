"""Day 20 Google Cloud: the DuckDB recursive-CTE twin of the GQL query equals the plain-Python walk model, loops are
flagged and never offered, the generated GQL uses a quantified path and the path functions, and the cloud
comparison accepts a match (including the fixed-length fallback) and rejects a wrong offer (no cloud)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-20-substitute-paths"
spec = importlib.util.spec_from_file_location("substitutes_day20", FEATURE / "substitutes.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)

LOCAL = sb.run_local()


def test_local_matches_pure_python():
    assert LOCAL == sb.pure_python()
    assert sb.compare(LOCAL, sb.pure_python()) == ([], [])


def test_counts_and_offers():
    s = sb.summarise(LOCAL)
    assert (s["walks_1_to_3_hops"], s["acyclic_walks"], s["looping_walks"]) == (33, 19, 14)
    picks = sb.pick_substitutes(LOCAL)
    assert picks["S1"] == {"offer": "A1", "hops": 1}
    assert picks["O2"] == {"offer": "A1", "hops": 2}
    assert picks["O1"] == {"offer": "A2", "hops": 3}       # A1 and A2 both 3 hops; A2 is closer in price
    assert picks["Y1"] == {"offer": "Y3", "hops": 2}
    assert picks["B1"] is None and picks["B2"] is None      # the bread pair only points at each other


def test_loops_are_never_offered():
    loops = [w for w in LOCAL if not w[4]]
    assert ("Y1", "Y1", False, 2, False, True) in loops     # Y1 -> Y2 -> Y1
    assert all(not w[5] or w[3] >= 2 for w in loops)
    assert all(w[1] != w[0] for w in LOCAL if w[4])


def test_gql_text():
    walks = sb.bigquery_sql("walks", "p.ds")
    assert walks.startswith("GRAPH `p.ds`.substitution_graph") and "->{1,3}(dst:Product)" in walks
    for fn in ("IS_ACYCLIC(p)", "IS_TRAIL(p)", "PATH_LENGTH(p)"):
        assert fn in walks
    setup = sb.bigquery_sql("setup", "p.ds")
    assert "CREATE OR REPLACE PROPERTY GRAPH `p.ds`.substitution_graph" in setup
    assert "SOURCE KEY (from_sku) REFERENCES products (sku)" in setup and "{" not in setup
    assert sb.bigquery_sql("fixed3", "p.ds").count("SubstitutesFor") == 3


def _bq_file(tmp_path, name, rows):
    f = tmp_path / name
    f.write_text("WARNING: --scopes ...\n" + json.dumps([dict(zip(sb.COLUMNS, [r[0], r[1], str(r[2]).lower(),
                                                                                 str(r[3]), r[4], r[5]]))
                                                           for r in rows]))
    return f


def test_read_and_compare(tmp_path):
    cloud = sb.read_rows(_bq_file(tmp_path, "walks.json", LOCAL))
    assert cloud == LOCAL
    # fixed-length fallback: three files, one per hop count
    parts = [sb.read_rows(_bq_file(tmp_path, f"f{h}.json", [w for w in LOCAL if w[3] == h])) for h in (1, 2, 3)]
    assert sorted(r for p in parts for r in p) == LOCAL
    # an engine that never reuses an edge drops some looping walks: warning only
    no_edge_reuse = [w for w in LOCAL if w[5]]
    errors, warnings = sb.compare(no_edge_reuse, LOCAL)
    assert errors == [] and len(warnings) == 1
    # a loop treated as acyclic changes what is offered: error
    wrong = [w if w[:4] != ("B1", "B1", False, 2) else ("B1", "B1", False, 2, True, True) for w in LOCAL]
    wrong.append(("B1", "A1", True, 3, True, True))
    errors, _ = sb.compare(wrong, LOCAL)
    assert len(errors) == 2
