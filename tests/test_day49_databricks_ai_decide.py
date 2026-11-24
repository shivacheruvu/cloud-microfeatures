"""Day 49 Databricks: the substitution rule against the hand-labelled key, the ai_decide SQL (constant questions JSON,
safe literals), and the structural check and scoring of Statement Execution API rows. No credentials."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-49-substitution-ai-decide"
spec = importlib.util.spec_from_file_location("substitutions49", FEATURE / "substitutions.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)


def test_rule_reproduces_key_and_result_json():
    ps = sb.pairs()
    assert len(ps) == 12
    for p in ps:
        assert sb.rule(p) == (p["verdict"], p["new_allergen"]), p["sub_no"]
    s = sb.summarise()
    assert s["rule_matches_key"] == 12 and s["verdicts"] == {"accept": 3, "ask_customer": 5, "reject": 4}
    saved = json.loads((FEATURE / "result.json").read_text())
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))


def test_rule_edges():
    p = dict(sb.pairs()[0])
    assert sb.rule({**p, "sub_size": 128 * 1.2})[0] == "accept"
    assert sb.rule({**p, "sub_size": 128 * 1.25})[0] == "ask_customer"
    assert sb.rule({**p, "sub_price": int(699 * 1.25)})[0] == "ask_customer"
    assert sb.rule({**p, "sub_allergens": ["milk", "soy"]}) == ("reject", True)
    assert sb.rule({**p, "sub_allergens": []}) == ("accept", False)   # losing an allergen is fine


def test_sql_shape():
    q = sb.sql()
    assert q.startswith("--") and q.count("ai_decide(state, '") == 1 and "to_json(" in q
    assert q.count("\n    (") == 12 and "AS t(sub_no, state)" in q
    qs = json.loads(sb.QUESTIONS_JSON)
    assert [v["type"] for v in qs.values()] == ["choice", "noul", "score"]
    assert set(qs["verdict"]["criteria"]) == set(sb.LABELS) and 2 <= len(qs["closeness"]["criteria"]) <= 10
    with pytest.raises(ValueError):
        sb._lit("Shiva's pie")


def _decision(verdict, p, c=2.0, err=None):
    return json.dumps({"response": {"answers": {
        "verdict": {"type": "choice", "choice": verdict, "confidence": 0.9},
        "new_allergen": {"type": "noul", "probability": p},
        "closeness": {"type": "score", "score": c}}}, "metadata": {"version": "1.0"}, "error_message": err})


def _rows(over=None, drop=()):
    rows = []
    for p in sb.pairs():
        if p["sub_no"] in drop:
            continue
        d = (over or {}).get(p["sub_no"], _decision(p["verdict"], 0.95 if p["new_allergen"] else 0.03))
        rows.append([str(p["sub_no"]), d])
    return rows


def test_check_and_score():
    got = sb.parse(_rows())
    assert sb.check(got) == []
    s = sb.score(got)
    assert s["verdicts_right"] == 12 and s["allergen_flags_right"] == 12 and s["mean_closeness"]["accept"] == 2.0
    off = sb.score(sb.parse(_rows({2: _decision("accept", 0.1), 4: _decision("reject", 0.2)})))
    assert off["verdicts_right"] == 11 and off["wrong_verdict"] == {2: "accept"} and off["wrong_allergen"] == [4]


def test_check_failures():
    assert sb.check(sb.parse(_rows(drop=(5,))))
    assert sb.check(sb.parse(_rows({1: _decision("maybe", 0.1)})))
    assert sb.check(sb.parse(_rows({1: _decision("accept", 1.4)})))
    assert sb.check(sb.parse(_rows({1: _decision("accept", 0.1, c=2.5)})))
    assert sb.check(sb.parse(_rows({1: json.dumps({"response": None, "error_message": "quota"})})))
    assert sb.check(sb.parse(_rows({1: json.dumps({"response": {"answers": {}}, "error_message": None})})))


def test_refusals_and_redaction(monkeypatch):
    assert sb.compute_refused("POST /api/2.0/sql/statements 403: FEATURE_DISABLED")
    assert sb.preview_off("[UNRESOLVED_ROUTINE] Cannot resolve routine `ai_decide`")
    assert not sb.preview_off("SQLSTATE 42703 column not found")
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-abc.cloud.databricks.com")
    assert sb._redact("dbc-abc.cloud.databricks.com me@x.com 12345678") == "<host> <user> <id>"


def test_run_warns_when_workspace_refuses(monkeypatch, capsys):
    def refuse(method, path, body=None):
        raise sb.Refused("GET /api/2.0/sql/warehouses 403: Organization 1234567890 is not active")
    monkeypatch.setattr(sb, "_api", refuse)
    assert sb.run_databricks() == 0
    out = capsys.readouterr().out
    assert "::warning" in out and "1234567890" not in out


def test_run_end_to_end_with_fake_api(monkeypatch, capsys):
    def api(method, path, body=None):
        if path == "/api/2.0/sql/warehouses":
            return {"warehouses": [{"id": "w1", "enable_serverless_compute": True}]}
        return {"status": {"state": "SUCCEEDED"}, "result": {"data_array": _rows()}}
    monkeypatch.setattr(sb, "_api", api)
    assert sb.run_databricks() == 0
    out = capsys.readouterr().out
    assert "Verdicts equal to the store rule 12/12" in out and "::warning" not in out
