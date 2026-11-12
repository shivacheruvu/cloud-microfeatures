"""Day 38 Google Cloud: the local JSON_FLATTEN model reproduces the documented examples, finds every item whatever the
packing depth while the fixed-depth unnest drops some, the SQL inlines every order escaped, and the compare step
accepts BigQuery-shaped output and catches a wrong count."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-38-tote-manifest-flatten"
spec = importlib.util.spec_from_file_location("tote_manifest38", FEATURE / "tote_manifest.py")
tm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tm)


def _bq_rows():  # bq's JSON gives INT64 as strings and BOOL as "true"/"false"
    return [{k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in r.items()}
            for r in tm.local_rows(tm.load_orders())]


def test_documented_examples():
    assert tm.json_flatten(1) == [1]
    assert tm.json_flatten([1, 2, None]) == [1, 2, None]
    assert tm.json_flatten([[[1]], 2, [3]]) == [1, 2, 3]
    assert tm.json_flatten({"a": [[1]]}) == [{"a": [[1]]}]
    assert tm.json_flatten([[[1, 2], 3], {"a": 4}, True]) == [1, 2, 3, {"a": 4}, True]
    assert tm.json_flatten(None) is None
    assert tm.check_edge_model() == []


def test_flatten_finds_every_item_fixed_depth_drops_some():
    orders = tm.load_orders()
    s = tm.summarise(tm.local_rows(orders), orders)
    assert s["orders"] == 8 and s["orders_with_totes"] == 7
    assert s["items"]["json_flatten"] == sum(s["items_by_depth"].values()) == 33
    assert s["items"]["fixed_depth_unnest"] == s["items_by_depth"]["depth 3"] == 25
    assert s["missed_by_fixed_depth"]["orders_short"] == ["W-1004", "W-1005", "W-1006", "W-1007"]
    assert s["by_order"]["W-1006"] == {"flatten": 3, "fixed": 0}
    assert s["by_order"]["W-1008"] == {"flatten": 0, "fixed": 0}
    rows = tm.local_rows(orders)
    assert all(r["flat_non_objects"] == 0 for r in rows)
    # substitutes stay inside their item: the sku sequence never contains a substitute that isn't also picked
    assert s["items_with_subs_not_double_counted"] == 6


def test_sql_inlines_every_order_escaped():
    q = tm.sql(tm.load_orders())
    assert "{orders}" not in q and q.count("AS doc)") == 8
    assert "JSON_FLATTEN(JSON_QUERY(o.doc, '$.totes'))" in q
    assert "\"status\":\"cancelled\"" in q
    assert tm._q("it's\na\\b") == "'it\\'s\\na\\\\b'"
    assert "JSON_FLATTEN(j)" in tm.main.__globals__["HERE"].joinpath("edge_cases.sql").read_text()


def test_compare_accepts_matching_rows(tmp_path, capsys):
    out = tmp_path / "out.json"
    out.write_text("Warning: --scopes ...\n" + json.dumps(_bq_rows()))
    assert tm.main("--compare", str(out)) == 0
    text = capsys.readouterr().out
    assert "::notice" in text and "::error" not in text and "33 items" in text and "8/8 orders" in text


def test_compare_catches_differences(tmp_path, capsys):
    orders = tm.load_orders()
    rows = _bq_rows()
    for col in ("flat_items", "flat_value_cents", "fixed_items"):
        rr = [dict(r) for r in rows]
        rr[4][col] = str(int(rr[4][col]) + 1)
        assert tm.check_cloud(rr, orders), col
    assert tm.check_cloud(rows[:-1], orders)
    out = tmp_path / "bad.json"
    out.write_text(json.dumps([dict(rows[0], flat_units="0")] + rows[1:]))
    assert tm.main("--compare", str(out)) == 1
    assert "::error" in capsys.readouterr().out


def test_edge_compare_reports_never_fails(tmp_path, capsys):
    out = tmp_path / "edge.json"
    rows = [{"label": k, "flattened": v} for k, v in tm.EDGE_CASES.items()]
    out.write_text(json.dumps(rows))
    assert tm.main("--edge-compare", str(out)) == 0
    assert "::notice" in capsys.readouterr().out
    rows[5]["flattened"] = None
    out.write_text(json.dumps(rows))
    assert tm.main("--edge-compare", str(out)) == 0
    assert "::warning" in capsys.readouterr().out


def test_result_json_is_current():
    orders = tm.load_orders()
    result = json.loads((FEATURE / "result.json").read_text())
    s = tm.summarise(tm.local_rows(orders), orders)
    assert {k: result[k] for k in s} == s
