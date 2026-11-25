"""Day 51 Google Cloud: the hand-labelled feedback comments and per-department answer key, the AI.AGG SQL with a typed
output_schema, and the cloud-output check (STRUCTs as JSON strings, a warning line before the JSON, model misses,
failure paths). No credentials."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-51-feedback-digest-ai-agg"
spec = importlib.util.spec_from_file_location("feedback_digest51", FEATURE / "feedback_digest.py")
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)


def test_answer_key_and_result_json():
    key = fd.answer_key()
    assert list(key) == ["bakery", "dairy", "meat", "produce"]
    assert {d: k["top_issue"] for d, k in key.items()} == {
        "bakery": "wait_time", "dairy": "price", "meat": "none", "produce": "out_of_stock"}
    assert key["meat"]["any_out_of_stock"] and key["meat"]["sentiment"] == "positive"  # the trap
    assert not key["dairy"]["any_out_of_stock"] and all(k["comments"] == 6 for k in key.values())
    saved = json.loads((FEATURE / "result.json").read_text())
    s = fd.summarise()
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))
    assert s["fields_checked"] == 12 and s["sold_out_mentions"] == 6


def test_sql_shape():
    q = fd.sql()
    assert q.count("AI.AGG(") == 1 and q.count("output_schema => '") == 1 and "GROUP BY department" in q
    assert "any_out_of_stock BOOL NOT NULL" in q and q.count("STRUCT(") == 24
    assert not q.lstrip().startswith("SELECT")  # starts with a comment: SQL must go in on stdin
    assert "'" not in fd.OUTPUT_SCHEMA and "'" not in fd.INSTRUCTION and '"' not in fd.INSTRUCTION
    assert all("'" not in c["comment"] and '"' not in c["comment"] for c in fd.comments())
    assert "\\u" not in q


def _digest(dept, **over):
    k = fd.answer_key()[dept]
    return {"top_issue": k["top_issue"], "sentiment": k["sentiment"], "any_out_of_stock": k["any_out_of_stock"],
            "headline": f"{dept} summary", **over}


def _out(tmp_path, digests=None, prefix="", drop=(), counts=None):
    rows = []
    for d in fd.answer_key():
        if d in drop:
            continue
        g = (digests or {}).get(d, _digest(d))
        rows.append({"department": d, "comments": str((counts or {}).get(d, 6)),
                     "digest": None if g is None else json.dumps(g)})
    p = tmp_path / "out.json"
    p.write_text(prefix + json.dumps(rows))
    return str(p)


def test_compare_pass(tmp_path, capsys):
    assert fd.compare(_out(tmp_path, prefix="WARNING: --scopes\n")) == 0
    out = capsys.readouterr().out
    assert "12/12 fields and 4/4 departments match" in out and "meat: none, positive, sold-out yes" in out
    assert "::warning" not in out and "headlines::bakery: bakery summary" in out


def test_spelling_and_string_bool_accepted(tmp_path, capsys):
    g = _digest("produce", top_issue="Out of stock", sentiment="NEGATIVE", any_out_of_stock="true")
    assert fd.compare(_out(tmp_path, {"produce": g})) == 0
    assert "12/12" in capsys.readouterr().out


def test_model_miss_is_warning(tmp_path, capsys):
    miss = {"meat": _digest("meat", top_issue="out_of_stock", any_out_of_stock=False)}
    assert fd.compare(_out(tmp_path, miss)) == 0
    out = capsys.readouterr().out
    assert "10/12 fields and 3/4 departments" in out
    assert "::warning" in out and "meat: top_issue=out_of_stock, any_out_of_stock=False" in out


def test_compare_failures(tmp_path, capsys):
    assert fd.compare(_out(tmp_path, {"dairy": _digest("dairy", top_issue="cost")})) == 1
    assert fd.compare(_out(tmp_path, {"dairy": _digest("dairy", sentiment="angry")})) == 1
    assert fd.compare(_out(tmp_path, {"dairy": _digest("dairy", any_out_of_stock=None)})) == 1
    assert fd.compare(_out(tmp_path, {"dairy": _digest("dairy", headline="")})) == 1
    assert fd.compare(_out(tmp_path, {"dairy": None})) == 1
    assert fd.compare(_out(tmp_path, drop=("bakery",))) == 1
    assert fd.compare(_out(tmp_path, counts={"meat": 5})) == 1
    out = capsys.readouterr().out
    assert out.count("::error") == 7
    p = tmp_path / "x.json"
    p.write_text("not json [")
    assert fd.compare(str(p)) == 1
