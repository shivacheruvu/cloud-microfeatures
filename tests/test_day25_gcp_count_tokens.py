"""Day 25 Google Cloud: the two shelf-tag templates, the local token estimate, the AI.COUNT_TOKENS SQL, and the
cloud-output check (both plausible return shapes, a warning line before the JSON, and the failure paths)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-25-shelf-blurb-token-budget"
spec = importlib.util.spec_from_file_location("token_budget", FEATURE / "token_budget.py")
tb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tb)


def test_prompts_and_estimate():
    rows = tb.prompts()
    assert len(rows) == 48 and {r["variant"] for r in rows} == {"short", "verbose"}
    assert all("{" not in r["prompt"] for r in rows)
    assert tb.estimate("Oat milk, 64 oz") == 1 + 1 + 1 + 1 + 1 + 1  # oat, milk, ",", 6, 4, oz
    assert tb.estimate("ingredients") == 3


def test_local_summary():
    s = tb.summarise(tb.local_counts())
    assert s["short_smaller_for_every_product"] and s["products"] == 24
    assert 50 < s["saving_pct"] < 80


def test_sql_counts_every_prompt_without_generating():
    q = tb.sql()
    assert q.count("STRUCT(") == 48 and "AI.COUNT_TOKENS(prompt, endpoint => 'gemini-3.1-flash-lite')" in q
    assert "AI.GENERATE" not in q and "--maximum" not in q


def _bq_out(tmp_path, scale=1.0, shape="struct", warning=True):
    rows = []
    for (b, v), n in tb.local_counts().items():
        n = max(1, round(n * scale))
        raw = json.dumps({"result": n}) if shape == "struct" else json.dumps(n)
        rows.append({"barcode": b, "variant": v, "raw": raw})
    p = tmp_path / "out.json"
    p.write_text(("WARNING: --scopes is ignored\n" if warning else "") + json.dumps(rows))
    return str(p)


def test_compare_accepts_both_shapes(tmp_path, capsys):
    assert tb.compare(_bq_out(tmp_path, 1.2, "struct")) == 0
    assert "::notice" in capsys.readouterr().out
    assert tb.compare(_bq_out(tmp_path, 0.8, "int", warning=False)) == 0


def test_compare_failures(tmp_path, capsys):
    assert tb.compare(_bq_out(tmp_path, 3.0)) == 1  # far from the estimate
    p = tmp_path / "null.json"
    p.write_text(json.dumps([{"barcode": b, "variant": v, "raw": "null"} for b, v in tb.local_counts()]))
    assert tb.compare(str(p)) == 1
    assert "without a positive count" in capsys.readouterr().out


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    s = tb.summarise(tb.local_counts())
    assert {k: result[k] for k in s} == s
