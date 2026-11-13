"""Day 39 Google Cloud: the answer key, the AI.GENERATE SQL on gemini-4-hydrogen with output_schema, and the
cloud-output check (STRUCTs as JSON strings, a warning line before the JSON, model misses, and the failure paths)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-39-shopping-list-hydrogen"
spec = importlib.util.spec_from_file_location("shopping39", FEATURE / "shopping_list.py")
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)


def test_answer_key_and_result_json():
    rows = sl.lines()
    assert len(rows) == 14 and all(r["unit"] in sl.UNITS for r in rows)
    key = {r["line_no"]: (r["quantity"], r["unit"]) for r in rows}
    assert key[3] == (6000, "ml") and key[11] == (13, "each") and key[14] == (18, "each")
    assert abs(key[5][0] - 1.5 * 453.592) < 0.001
    saved = json.loads((FEATURE / "result.json").read_text())
    s = sl.summarise()
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))


def test_sql_shape():
    q = sl.sql()
    assert q.count("AI.GENERATE(") == 2 and "endpoint => 'gemini-4-hydrogen'" in q
    assert "endpoint => 'gemini-3.5-flash-lite'" in q and q.count("output_schema => '") == 2
    assert q.count("STRUCT(") == 14 + 2 and not q.lstrip().startswith("SELECT")  # starts with a comment
    assert "'" not in sl.OUTPUT_SCHEMA and "'" not in sl.INSTRUCTION and '"' not in sl.INSTRUCTION
    assert "a baker\\u0027s" not in q and "a baker's dozen bagels" in q


def _answer(r, **over):
    return {"item": r["item"], "quantity": r["quantity"], "unit": r["unit"], "status": "", **over}


def _out(tmp_path, hydrogen=None, baseline=None, prefix="", drop=()):
    rows = []
    for r in sl.lines():
        n = r["line_no"]
        if n in drop:
            continue
        h = (hydrogen or {}).get(n, _answer(r))
        b = (baseline or {}).get(n, _answer(r))
        rows.append({"line_no": str(n), "hydrogen": None if h is None else json.dumps(h),
                     "baseline": None if b is None else json.dumps(b)})
    p = tmp_path / "out.json"
    p.write_text(prefix + json.dumps(rows))
    return str(p)


def test_compare_pass(tmp_path, capsys):
    assert sl.compare(_out(tmp_path, prefix="WARNING: --scopes\n")) == 0
    out = capsys.readouterr().out
    assert "14/14 equal the answer key" in out and "gemini-3.5-flash-lite 14/14" in out and "::warning" not in out


def test_tolerance_and_unit_case():
    r = sl.lines()[4]  # 680.388 g
    assert sl.correct({"quantity": 680.4, "unit": "G", "status": ""}, r)
    assert sl.correct({"quantity": "680", "unit": "g"}, r)
    assert not sl.correct({"quantity": 1.5, "unit": "lb"}, r)
    assert not sl.correct({"quantity": 700, "unit": "g"}, r)
    assert not sl.correct({"quantity": 680.4, "unit": "g", "status": "quota"}, r)


def test_model_miss_is_warning(tmp_path, capsys):
    r = sl.lines()[13]
    bad = {14: _answer(r, quantity=12)}
    assert sl.compare(_out(tmp_path, hydrogen=bad, baseline={11: None})) == 0
    out = capsys.readouterr().out
    assert "13/14" in out and "::warning" in out and "line(s) 14" in out and "gemini-3.5-flash-lite 13/14" in out


def test_compare_failures(tmp_path, capsys):
    r = sl.lines()[0]
    assert sl.compare(_out(tmp_path, hydrogen={1: _answer(r, status="Endpoint not found")})) == 1
    assert sl.compare(_out(tmp_path, hydrogen={1: _answer(r, unit="dozen")})) == 1
    assert sl.compare(_out(tmp_path, hydrogen={1: _answer(r, quantity=None)})) == 1
    assert sl.compare(_out(tmp_path, hydrogen={1: None})) == 1
    assert sl.compare(_out(tmp_path, drop=(7,))) == 1
    out = capsys.readouterr().out
    assert out.count("::error") == 5
    p = tmp_path / "x.json"
    p.write_text("not json [")
    assert sl.compare(str(p)) == 1
