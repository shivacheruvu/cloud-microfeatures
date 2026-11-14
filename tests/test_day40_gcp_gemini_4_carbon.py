"""Day 40 Google Cloud: the allergen answer key, the AI.GENERATE SQL on gemini-4-carbon with a six-BOOL output_schema,
and the cloud-output check (STRUCTs as JSON strings, a warning line before the JSON, model misses, failure paths)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-40-allergen-flags-carbon"
spec = importlib.util.spec_from_file_location("allergens40", FEATURE / "allergens.py")
al = importlib.util.module_from_spec(spec)
spec.loader.exec_module(al)


def test_answer_key_and_result_json():
    rows = {r["product_no"]: r for r in al.products()}
    assert len(rows) == 14
    assert rows[4]["milk"] and rows[4]["tree_nut"] and rows[4]["soy"] and not rows[4]["peanut"]  # whey, cashew, lecithin
    assert rows[5]["wheat"] and rows[5]["egg"]  # semolina
    assert not rows[6]["milk"] and not rows[6]["tree_nut"] and rows[6]["peanut"]  # coconut milk, peanuts
    assert not any(rows[7][f] for f in al.FLAGS)  # vegan cheddar: coconut oil
    saved = json.loads((FEATURE / "result.json").read_text())
    s = al.summarise()
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))


def test_sql_shape():
    q = al.sql()
    assert q.count("AI.GENERATE(") == 2 and "endpoint => 'gemini-4-carbon'" in q
    assert "endpoint => 'gemini-3.5-flash-lite'" in q and q.count("output_schema => '") == 2
    assert q.count(" BOOL OPTIONS(") == 12 and q.count("STRUCT(") == 14 + 2
    assert not q.lstrip().startswith("SELECT")  # starts with a comment: SQL must go in on stdin
    assert "'" not in al.OUTPUT_SCHEMA and "'" not in al.INSTRUCTION and '"' not in al.INSTRUCTION
    assert "\\u" not in q


def _answer(r, **over):
    return {**{f: r[f] for f in al.FLAGS}, "status": "", **over}


def _out(tmp_path, carbon=None, baseline=None, prefix="", drop=()):
    rows = []
    for r in al.products():
        n = r["product_no"]
        if n in drop:
            continue
        c = (carbon or {}).get(n, _answer(r))
        b = (baseline or {}).get(n, _answer(r))
        rows.append({"product_no": str(n), "carbon": None if c is None else json.dumps(c),
                     "baseline": None if b is None else json.dumps(b)})
    p = tmp_path / "out.json"
    p.write_text(prefix + json.dumps(rows))
    return str(p)


def test_compare_pass(tmp_path, capsys):
    assert al.compare(_out(tmp_path, prefix="WARNING: --scopes\n")) == 0
    out = capsys.readouterr().out
    assert "gemini-4-carbon 84/84 flags, 14/14 products" in out and "gemini-3.5-flash-lite 84/84" in out
    assert "::warning" not in out


def test_string_booleans_accepted(tmp_path, capsys):
    r = al.products()[0]
    assert al.compare(_out(tmp_path, carbon={1: {f: str(r[f]).lower() for f in al.FLAGS}})) == 0
    assert "84/84" in capsys.readouterr().out


def test_model_miss_is_warning(tmp_path, capsys):
    rows = al.products()
    miss = {4: _answer(rows[3], milk=False), 6: _answer(rows[5], tree_nut=True)}
    assert al.compare(_out(tmp_path, carbon=miss, baseline={11: None})) == 0
    out = capsys.readouterr().out
    assert "gemini-4-carbon 82/84 flags, 12/14 products" in out
    assert "missed allergens: 4:milk" in out and "false alarms: 6:tree_nut" in out
    assert "gemini-3.5-flash-lite 78/84" in out and "::warning" in out


def test_compare_failures(tmp_path, capsys):
    r = al.products()[0]
    assert al.compare(_out(tmp_path, carbon={1: _answer(r, status="Endpoint not found")})) == 1
    assert al.compare(_out(tmp_path, carbon={1: _answer(r, soy=None)})) == 1
    assert al.compare(_out(tmp_path, carbon={1: _answer(r, egg="maybe")})) == 1
    assert al.compare(_out(tmp_path, carbon={1: None})) == 1
    assert al.compare(_out(tmp_path, drop=(7,))) == 1
    out = capsys.readouterr().out
    assert out.count("::error") == 5
    p = tmp_path / "x.json"
    p.write_text("not json [")
    assert al.compare(str(p)) == 1
