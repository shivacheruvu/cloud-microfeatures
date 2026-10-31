"""Day 26 Google Cloud: the trigram baseline, the AI.SIMILARITY SQL, and the cloud-output check (scores as numbers
or JSON strings, a warning line before the JSON, a weaker model, and the failure paths)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-26-receipt-line-matcher"
spec = importlib.util.spec_from_file_location("matcher", FEATURE / "matcher.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_data_and_answer_key():
    codes = {p["barcode"] for p in m.catalog()}
    assert len(codes) == 16 and all(ln["expected_barcode"] in codes for ln in m.lines())
    assert len({ln["expected_barcode"] for ln in m.lines()}) == 16  # one line per product


def test_trigram_cosine():
    assert abs(m.trigram_cosine("Oat milk", "OAT MILK") - 1.0) < 1e-9
    assert m.trigram_cosine("EVOO", "Organic Bananas") == 0.0
    assert m.trigram_cosine("ORG BNNA", "Organic Bananas") > m.trigram_cosine("ORG BNNA", "Orange Juice Pulp Free 52 oz")


def test_baseline_misses_what_has_no_shared_letters():
    s = m.summarise(m.local_scores())
    assert s["pairs_scored"] == 256 and s["top1_correct"] == 14
    assert set(s["wrong"]) == {"EVOO", "BATH TISSUE 12PK"}


def test_sql_scores_every_pair():
    q = m.sql()
    assert q.count("STRUCT(") == 16 and "CROSS JOIN UNNEST" in q
    assert "AI.SIMILARITY(content1 => line, content2 => name, endpoint => 'gemini-embedding-2')" in q
    assert q.lstrip().startswith("--") and "--maximum" not in q


def _bq_out(tmp_path, scores, as_string=True, warning=True):
    rows = [{"line": ln, "barcode": b, "raw": json.dumps(s) if as_string else s} for (ln, b), s in scores.items()]
    p = tmp_path / "out.json"
    p.write_text(("WARNING: --scopes is ignored\n" if warning else "") + json.dumps(rows))
    return str(p)


def _perfect():
    return {(ln["line"], p["barcode"]): (0.9 if p["barcode"] == ln["expected_barcode"] else 0.3)
            for ln in m.lines() for p in m.catalog()}


def test_compare_reports_matches(tmp_path, capsys):
    assert m.compare(_bq_out(tmp_path, _perfect())) == 0
    out = capsys.readouterr().out
    assert "::notice" in out and "16 of 16 lines" in out and "14 of 16" in out and "::warning" not in out
    assert m.compare(_bq_out(tmp_path, _perfect(), as_string=False, warning=False)) == 0


def test_weaker_model_is_a_warning_not_a_failure(tmp_path, capsys):
    flat = {k: 0.5 for k in _perfect()}  # every product ties: all go to the lowest barcode
    assert m.compare(_bq_out(tmp_path, flat)) == 0
    assert "::warning" in capsys.readouterr().out


def test_compare_failures(tmp_path, capsys):
    s = _perfect()
    s[next(iter(s))] = None
    assert m.compare(_bq_out(tmp_path, s)) == 1
    assert "without a score" in capsys.readouterr().out
    s = _perfect()
    s.pop(next(iter(s)))
    assert m.compare(_bq_out(tmp_path, s)) == 1
    s = _perfect()
    s[next(iter(s))] = 1.7
    assert m.compare(_bq_out(tmp_path, s)) == 1


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    s = m.summarise(m.local_scores())
    assert {k: result[k] for k in s} == s
