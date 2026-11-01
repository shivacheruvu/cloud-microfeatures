"""Day 27 Google Cloud: the answer key, the AI.AGG SQL with output_schema, and the cloud-output check (STRUCTs as JSON
strings, a warning line before the JSON, a disagreeing model, and the failure paths)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-27-store-feedback-themes"
spec = importlib.util.spec_from_file_location("themes27", FEATURE / "themes.py")
th = importlib.util.module_from_spec(spec)
spec.loader.exec_module(th)


def test_answer_key_and_result_json():
    key = th.answer_key()
    assert {s: (v["top_issue"], v["mentions"]) for s, v in key.items()} == {
        "east": ("checkout_wait", 6), "north": ("out_of_stock", 6), "south": ("produce_quality", 5)}
    assert all(r["issue"] in th.CATEGORIES for r in th.comments())
    saved = json.loads((FEATURE / "result.json").read_text())
    assert saved["answer_key"] == key and saved["comments"] == 24


def test_sql_shape():
    q = th.sql()
    assert "AI.AGG(" in q and "output_schema => '" in q and f"endpoint => '{th.ENDPOINT}'" in q
    assert "GROUP BY store" in q and q.count("STRUCT(") == 24
    # output_schema sits in a single-quoted literal: it must not contain a single quote
    assert "'" not in th.OUTPUT_SCHEMA and all(f"{f} " in th.OUTPUT_SCHEMA for f in th.FIELDS)
    assert th.OUTPUT_SCHEMA.count("NOT NULL") == 4


def _out(tmp_path, themes_by_store, prefix=""):
    rows = [{"store": s, "comments": "8", "themes": None if t is None else json.dumps(t)}
            for s, t in themes_by_store.items()]
    p = tmp_path / "out.json"
    p.write_text(prefix + json.dumps(rows))
    return str(p)


def _good():
    return {s: {"top_issue": v["top_issue"], "mentions": v["mentions"], "summary": "s", "suggested_fix": "f"}
            for s, v in th.answer_key().items()}


def test_compare_pass(tmp_path, capsys):
    assert th.compare(_out(tmp_path, _good(), "WARNING: --scopes\n")) == 0
    out = capsys.readouterr().out
    assert "agrees with the answer key for 3 of 3" in out and "::warning" not in out


def test_compare_disagreement_is_warning(tmp_path, capsys):
    t = _good()
    t["south"]["top_issue"] = "price"
    assert th.compare(_out(tmp_path, t)) == 0
    assert "::warning" in capsys.readouterr().out


def test_compare_failures(tmp_path, capsys):
    t = _good()
    t["east"]["top_issue"] = "long lines"
    assert th.compare(_out(tmp_path, t)) == 1
    t = _good()
    t["north"]["mentions"] = 40
    assert th.compare(_out(tmp_path, t)) == 1
    t = _good()
    t["north"] = None
    assert th.compare(_out(tmp_path, t)) == 1
    t = _good()
    del t["south"]
    assert th.compare(_out(tmp_path, t)) == 1
    assert capsys.readouterr().out.count("::error") == 4
