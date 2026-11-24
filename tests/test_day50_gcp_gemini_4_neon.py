"""Day 50 Google Cloud: the pack-size answer key and unit-price labels, the AI.GENERATE SQL on gemini-4-neon with a
typed output_schema, and the cloud-output check (STRUCTs as JSON strings, a warning line before the JSON, model misses,
failure paths). No credentials."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-50-unit-price-labels-neon"
spec = importlib.util.spec_from_file_location("unit_prices50", FEATURE / "unit_prices.py")
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)


def test_answer_key_labels_and_result_json():
    rows = {r["product_no"]: r for r in up.products()}
    assert len(rows) == 14
    assert (rows[4]["pack_count"], rows[4]["amount"], rows[4]["unit"]) == (12, 355.0, "ml")
    assert (rows[12]["pack_count"], rows[12]["amount"], rows[12]["unit"]) == (1, 12.0, "each")  # a dozen
    assert rows[13]["pack_count"] == 6 and rows[7]["unit"] == "ml"  # six-pack; bracketed metric size
    assert up.label(599, 12, 355, "ml") == "$0.14 / 100 ml"
    assert up.label(2388, 1, 12, "lb") == "$0.44 / 100 g"
    assert up.label(459, 1, 12, "each") == "$0.38 / item"
    assert up.label(899, 1, 0.5, "kg") == up.label(899, 1, 500, "g") == "$1.80 / 100 g"
    saved = json.loads((FEATURE / "result.json").read_text())
    s = up.summarise()
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))
    assert s["label_families"] == {"100 g": 7, "100 ml": 5, "item": 2}


def test_sql_shape():
    q = up.sql()
    assert q.count("AI.GENERATE(") == 2 and "endpoint => 'gemini-4-neon'" in q
    assert "endpoint => 'gemini-3.5-flash-lite'" in q and q.count("output_schema => '") == 2
    assert q.count("pack_count INT64") == 2 and q.count("STRUCT(") == 14 + 2
    assert not q.lstrip().startswith("SELECT")  # starts with a comment: SQL must go in on stdin
    assert "'" not in up.OUTPUT_SCHEMA and "'" not in up.INSTRUCTION and '"' not in up.INSTRUCTION
    assert "\\u" not in q


def _answer(r, **over):
    return {"pack_count": r["pack_count"], "amount": r["amount"], "unit": r["unit"], "status": "", **over}


def _out(tmp_path, neon=None, baseline=None, prefix="", drop=()):
    rows = []
    for r in up.products():
        n = r["product_no"]
        if n in drop:
            continue
        c = (neon or {}).get(n, _answer(r))
        b = (baseline or {}).get(n, _answer(r))
        rows.append({"product_no": str(n), "neon": None if c is None else json.dumps(c),
                     "baseline": None if b is None else json.dumps(b)})
    p = tmp_path / "out.json"
    p.write_text(prefix + json.dumps(rows))
    return str(p)


def test_compare_pass(tmp_path, capsys):
    assert up.compare(_out(tmp_path, prefix="WARNING: --scopes\n")) == 0
    out = capsys.readouterr().out
    assert "gemini-4-neon 42/42 fields, 14/14 products fully right, 14/14 unit-price labels" in out
    assert "gemini-3.5-flash-lite 42/42" in out and "::warning" not in out


def test_string_numbers_and_unit_spelling_accepted(tmp_path, capsys):
    r = up.products()[2]
    assert up.compare(_out(tmp_path, neon={3: _answer(r, pack_count="1", amount="32", unit="FL OZ")})) == 0
    assert "42/42" in capsys.readouterr().out


def test_model_miss_is_warning(tmp_path, capsys):
    rows = up.products()
    # eggs read as 1 x 1 dozen-ish "each" (wrong amount); butter read as 1 x 16 oz (fields wrong, same label)
    miss = {12: _answer(rows[11], amount=1), 6: _answer(rows[5], pack_count=1, amount=16)}
    assert up.compare(_out(tmp_path, neon=miss, baseline={4: None})) == 0
    out = capsys.readouterr().out
    assert "gemini-4-neon 39/42 fields, 12/14 products fully right, 13/14 unit-price labels right" in out
    assert "gemini-3.5-flash-lite 39/42 fields, 13/14" in out and "::warning" in out and "12: 1 x 1 each" in out


def test_compare_failures(tmp_path, capsys):
    r = up.products()[0]
    assert up.compare(_out(tmp_path, neon={1: _answer(r, status="Endpoint not found")})) == 1
    assert up.compare(_out(tmp_path, neon={1: _answer(r, unit="stone")})) == 1
    assert up.compare(_out(tmp_path, neon={1: _answer(r, pack_count=1.5)})) == 1
    assert up.compare(_out(tmp_path, neon={1: _answer(r, amount=0)})) == 1
    assert up.compare(_out(tmp_path, neon={1: None})) == 1
    assert up.compare(_out(tmp_path, drop=(7,))) == 1
    out = capsys.readouterr().out
    assert out.count("::error") == 6
    p = tmp_path / "x.json"
    p.write_text("not json [")
    assert up.compare(str(p)) == 1
