"""Day 47 Google Cloud: the diet-badge answer key, the multi-label AI.CLASSIFY SQL (three call forms), and the
cloud-output check (arrays as JSON strings, a warning line before the JSON, empty badge sets, model misses, failures)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-47-shelf-badges-multilabel"
spec = importlib.util.spec_from_file_location("badges47", FEATURE / "badges.py")
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)


def test_answer_key_consistent_and_result_json():
    rows = {r["product_no"]: r for r in bd.products()}
    assert len(rows) == 14
    for r in rows.values():
        assert set(r["badges"]) <= set(bd.BADGES)
        if "vegan" in r["badges"]:  # vegan implies vegetarian and dairy-free
            assert {"vegetarian", "dairy_free"} <= set(r["badges"])
    assert rows[4]["badges"] == []  # chicken caesar wrap: no badge at all
    assert "vegan" not in rows[3]["badges"] and "gluten_free" in rows[3]["badges"]  # honey; certified oats
    assert "gluten_free" not in rows[12]["badges"]  # plain oats
    assert "contains_nuts" in rows[10]["badges"] and "contains_nuts" not in rows[13]["badges"]  # peanuts; coconut
    saved = json.loads((FEATURE / "result.json").read_text())
    s = bd.summarise()
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))
    assert s["badges_in_key"] == 36 and s["single_label_max_recall"] == round(13 / 36, 3)


def test_sql_forms():
    for n in (1, 2, 3):
        q = bd.sql(n)
        assert not q.lstrip().startswith("SELECT")  # starts with a comment: SQL must go in on stdin
        assert q.count("output_mode => 'multi')") == 1 and q.count("STRUCT(") == 14
        assert "\\u" not in q
    assert bd.sql(1).count("AI.CLASSIFY(") == 2 and '("vegan", "no animal' in bd.sql(1)
    assert '["vegan", "vegetarian", "gluten_free", "dairy_free", "contains_nuts"]' in bd.sql(2)
    assert "Badge definitions:" in bd.sql(2) and bd.sql(3).count("AI.CLASSIFY(") == 1 and "NULL AS single" in bd.sql(3)


def _out(tmp_path, multi=None, single=None, prefix="", drop=()):
    rows = []
    for r in bd.products():
        n = r["product_no"]
        if n in drop:
            continue
        m = (multi or {}).get(n, r["badges"])
        s = (single or {}).get(n, r["badges"][0] if r["badges"] else "vegan")
        rows.append({"product_no": str(n), "multi": None if m is None else json.dumps(m),
                     "single": None if s is None else json.dumps(s)})
    p = tmp_path / "out.json"
    p.write_text(prefix + json.dumps(rows))
    return str(p)


def test_compare_pass(tmp_path, capsys):
    assert bd.compare(_out(tmp_path, prefix="WARNING: --scopes\n"), 1) == 0
    out = capsys.readouterr().out
    assert "precision 1.0, recall 1.0, F1 1.0 over 36 badges; 14/14 products" in out
    assert "Single-label AI.CLASSIFY on the same input: 13/36 badges" in out and "::warning" not in out


def test_label_spelling_normalised(tmp_path, capsys):
    assert bd.compare(_out(tmp_path, multi={1: ["Vegan", "vegetarian", "gluten-free", "dairy free"]}), 2) == 0
    assert "14/14 products" in capsys.readouterr().out


def test_model_misses_and_low_f1_warning(tmp_path, capsys):
    assert bd.compare(_out(tmp_path, multi={3: ["vegetarian", "dairy_free", "contains_nuts"], 4: ["vegetarian"]}), 1) == 0
    out = capsys.readouterr().out
    assert "missed: 3:gluten_free" in out and "extra: 4:vegetarian" in out and "12/14 products" in out
    assert "::warning" not in out
    bad = {r["product_no"]: [] for r in bd.products()}
    assert bd.compare(_out(tmp_path, multi=bad), 3) == 0
    out = capsys.readouterr().out
    assert "F1 0.0" in out and "::warning" in out and "baseline not run" in out


def test_compare_failures(tmp_path, capsys):
    assert bd.compare(_out(tmp_path, multi={1: None}), 1) == 1
    assert bd.compare(_out(tmp_path, multi={1: "vegan"}), 1) == 1  # a STRING: single-label came back
    assert bd.compare(_out(tmp_path, multi={1: ["vegan", "keto"]}), 1) == 1
    assert bd.compare(_out(tmp_path, drop=(7,)), 1) == 1
    out = capsys.readouterr().out
    assert out.count("::error") == 4
    p = tmp_path / "x.json"
    p.write_text("not json [")
    assert bd.compare(str(p), 1) == 1
