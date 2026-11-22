"""Day 48 Google Cloud: the pure-Python JSON_FLATTEN reference (checked against the reference page's examples),
the basket figures, the SQL, and the cloud-output check (bq's string numbers, a warning line, NULLs, failures)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-48-basket-lines-json-flatten"
spec = importlib.util.spec_from_file_location("basket48", FEATURE / "basket.py")
bk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bk)


def test_flatten_matches_reference_page_examples():
    assert bk.flatten(1) == [1]
    assert bk.flatten([1, 2, None]) == [1, 2, None]
    assert bk.flatten([[[1]], 2, [3]]) == [1, 2, 3]
    assert bk.flatten({"a": [[1]]}) == [{"a": [[1]]}]
    assert bk.flatten([[[1, 2], 3], {"a": 4}, True]) == [1, 2, 3, {"a": 4}, True]
    assert bk.flatten(None) is None and bk.flatten([[], [[]]]) == []


def test_baskets_and_result_json():
    e = bk.expected()
    assert e[1]["total_cents"] == 2 * 129 + 249 and e[1]["units"] == 3
    assert e[2]["skus"] == "BANANA,SANDWICH-BLT,CRISPS-SALTED,WATER-500ML" and e[2]["total_cents"] == 500
    assert e[3]["skus"] == "YOGURT-4PK,GRANOLA,COFFEE-BEANS"
    assert e[4]["line_count"] == 1 and e[4]["total_cents"] == 389
    assert e[5]["flat"][0]["subs"] == [["TOMATO-VINE"], ["TOMATO-CHERRY"]] and e[5]["line_count"] == 2
    assert e[7] == {"flat": [], "line_count": 0, "units": None, "total_cents": None, "skus": None}
    assert e[8]["flat"] is None and e[8]["line_count"] == 0
    saved = json.loads((FEATURE / "result.json").read_text())
    s = bk.summarise()
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))
    assert s["orders"] == 8 and s["lines"] == 17 and s["max_depth"] == 3


def test_sql():
    q = bk.sql()
    assert not q.lstrip().startswith("WITH") and q.count("STRUCT(") == 8
    assert q.count("JSON_FLATTEN(lines)") == 5 and "CAST(NULL AS JSON)" in q
    assert "PARSE_JSON('{\"sku\":\"EGGS-12\"" in q


def _out(tmp_path, change=None, prefix="", drop=()):
    rows = []
    for n, e in bk.expected().items():
        if n in drop:
            continue
        r = {"order_id": str(n), "flat": None if e["flat"] is None else json.dumps(e["flat"]),
             "line_count": str(e["line_count"]),
             "units": None if e["units"] is None else str(e["units"]),
             "total_cents": None if e["total_cents"] is None else str(e["total_cents"]), "skus": e["skus"]}
        r.update((change or {}).get(n, {}))
        rows.append(r)
    p = tmp_path / "out.json"
    p.write_text(prefix + json.dumps(rows))
    return str(p)


def test_compare_pass(tmp_path, capsys):
    assert bk.compare(_out(tmp_path, prefix="WARNING: --scopes\n")) == 0
    out = capsys.readouterr().out
    assert "into 17 line objects" in out and "::error" not in out


def test_compare_failures(tmp_path, capsys):
    assert bk.compare(_out(tmp_path, change={3: {"flat": json.dumps([[{"sku": "YOGURT-4PK"}]])}})) == 1
    assert bk.compare(_out(tmp_path, change={5: {"line_count": "4"}})) == 1  # subs opened up: wrong
    assert bk.compare(_out(tmp_path, change={2: {"skus": "SANDWICH-BLT,BANANA,CRISPS-SALTED,WATER-500ML"}})) == 1
    assert bk.compare(_out(tmp_path, change={8: {"flat": "[]"}})) == 1  # NULL must stay NULL
    assert bk.compare(_out(tmp_path, drop=(4,))) == 1
    out = capsys.readouterr().out
    assert out.count("::error") == 5
    p = tmp_path / "x.json"
    p.write_text("not json [")
    assert bk.compare(str(p)) == 1
