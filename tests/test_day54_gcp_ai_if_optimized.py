"""Day 54 Google Cloud: the review-triage answer key, the keyword baseline, the AI.IF optimized-mode SQL (two call
forms) and the cloud-output check (BOOLs as strings, a warning line before the JSON, NULLs, misses, failures)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-54-review-triage-ai-if-optimized"
spec = importlib.util.spec_from_file_location("triage54", FEATURE / "triage.py")
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)


def test_answer_key_and_result_json():
    rows = {r["review_no"]: r for r in t.reviews()}
    assert len(rows) == 40 and sum(r["problem"] for r in rows.values()) == 20
    assert rows[4]["problem"] is False and rows[10]["problem"] is True  # late delivery; wrong item
    assert rows[24]["problem"] is False and rows[25]["problem"] is True  # "not a single bad berry"; short count
    saved = json.loads((FEATURE / "result.json").read_text())
    s = t.local_summary()
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))


def test_keyword_baseline_is_generic():
    assert t.keyword_flag("Blueberries: not a single bad berry")      # false positive the rule can't avoid
    assert not t.keyword_flag("Bananas: perfectly ripe, exactly how I like them")
    assert t.keyword_flag("Strawberries: half the box was moldy")    # prefix match
    assert t.local_summary()["keyword_baseline"]["accuracy"] < 0.7


def test_sql_forms():
    q1, q2 = t.sql(1), t.sql(2)
    for q in (q1, q2):
        assert not q.lstrip().startswith("SELECT")  # starts with a comment: SQL must go in on stdin
        assert q.count("optimization_mode => 'MINIMIZE_COST')") == 1 and q.count("STRUCT(") == 40
        assert "max_error_ratio" not in q            # not supported together with MINIMIZE_COST
    assert q1.count("AI.IF(") == 2 and "optimization_mode => 'MAXIMIZE_QUALITY'" in q1
    assert q2.count("AI.IF(") == 1 and "NULL AS quality" in q2


def _out(tmp_path, cost=None, quality=None, prefix="", drop=()):
    rows = []
    for r in t.reviews():
        n = r["review_no"]
        if n in drop:
            continue
        c = (cost or {}).get(n, r["problem"])
        q = (quality or {}).get(n, r["problem"])
        rows.append({"review_no": str(n), "cost": None if c is None else str(c).lower(),
                     "quality": None if q is None else str(q).lower()})
    p = tmp_path / "out.json"
    p.write_text(prefix + json.dumps(rows))
    return str(p)


def test_compare_pass(tmp_path, capsys):
    assert t.compare(_out(tmp_path, prefix="WARNING: --scopes\n"), 1) == 0
    out = capsys.readouterr().out
    assert "accuracy 1.0, precision 1.0, recall 1.0, 20 flagged, 0 NULL" in out
    assert "agree on 40/40" in out and "::warning" not in out


def test_misses_disagreement_and_warning(tmp_path, capsys):
    assert t.compare(_out(tmp_path, cost={1: False, 2: True}, quality={1: True}), 1) == 0
    out = capsys.readouterr().out
    assert "wrong: 1, 2." in out and "agree on 38/40" in out
    flipped = {r["review_no"]: not r["problem"] for r in t.reviews()}
    assert t.compare(_out(tmp_path, cost=flipped), 2) == 0
    out = capsys.readouterr().out
    assert "accuracy 0.0" in out and "::warning" in out and "MAXIMIZE_QUALITY" not in out


def test_compare_failures(tmp_path, capsys):
    assert t.compare(_out(tmp_path, cost={n: None for n in range(1, 6)}), 1) == 1   # 5 NULLs
    assert t.compare(_out(tmp_path, cost={1: "maybe"}), 1) == 1
    assert t.compare(_out(tmp_path, drop=(7,)), 1) == 1
    out = capsys.readouterr().out
    assert out.count("::error") == 3
    assert t.compare(_out(tmp_path, cost={1: None, 2: None}), 1) == 0              # a few NULLs are reported
    assert "2 NULL" in capsys.readouterr().out
    p = tmp_path / "x.json"
    p.write_text("not json [")
    assert t.compare(str(p), 1) == 1
