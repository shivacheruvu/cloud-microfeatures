"""Day 53 Google Cloud: the trigram baseline, the AI.SIMILARITY SQL for embeddinggemma-300m, and the cloud-output
check (scores as numbers or JSON strings, a warning line before the JSON, the comparison run, failure paths)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-53-search-synonyms-gemma"
spec = importlib.util.spec_from_file_location("matcher53", FEATURE / "matcher.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_data_and_answer_key():
    skus = {p["sku"] for p in m.catalog()}
    assert len(skus) == 16 and all(s["expected_sku"] in skus for s in m.searches())
    assert len({s["expected_sku"] for s in m.searches()}) == 16


def test_baseline_gets_only_the_shared_word_half():
    s = m.summarise(m.local_scores())
    assert s["pairs_scored"] == 256 and s["top1_correct"] == 7
    assert {"courgette", "nappies", "crisps", "garbanzo beans", "aubergine"} <= set(s["wrong"])
    assert s["wrong"]["aubergine"] == "(no match)"  # a zero-score tie is not a match
    assert "porridge oats" not in s["wrong"] and "streaky bacon" not in s["wrong"]


def test_sql_scores_every_pair_with_the_named_endpoint():
    q = m.sql()
    assert q.count("STRUCT(") == 16 and "CROSS JOIN UNNEST" in q
    assert "AI.SIMILARITY(content1 => search, content2 => name, endpoint => 'embeddinggemma-300m')" in q
    assert "endpoint => 'gemini-embedding-2'" in m.sql("gemini-embedding-2")
    assert q.lstrip().startswith("--") and "--maximum" not in q


def _bq_out(tmp_path, scores, name="out.json", as_string=True, warning=True):
    rows = [{"search": t, "sku": k, "raw": json.dumps(s) if as_string else s} for (t, k), s in scores.items()]
    p = tmp_path / name
    p.write_text(("WARNING: --scopes is ignored\n" if warning else "") + json.dumps(rows))
    return str(p)


def _perfect():
    return {(s["search"], p["sku"]): (0.8 if p["sku"] == s["expected_sku"] else 0.4)
            for s in m.searches() for p in m.catalog()}


def test_compare_reports_both_models(tmp_path, capsys):
    assert m.compare(_bq_out(tmp_path, _perfect()), _bq_out(tmp_path, _perfect(), "g.json", as_string=False)) == 0
    out = capsys.readouterr().out
    assert "16 of 16 searches" in out and "7 of 16 for the local trigram baseline" in out
    assert "16 of 16 for gemini-embedding-2" in out and "::warning" not in out


def test_weaker_model_or_broken_comparison_is_a_warning(tmp_path, capsys):
    flat = {k: 0.5 for k in _perfect()}
    bad = tmp_path / "bad.json"
    bad.write_text("not json [")
    assert m.compare(_bq_out(tmp_path, flat), str(bad)) == 0
    out = capsys.readouterr().out
    assert out.count("::warning") == 2


def test_compare_failures(tmp_path, capsys):
    for change in ("none", "missing", "range"):
        s = _perfect()
        k = next(iter(s))
        if change == "none":
            s[k] = None
        elif change == "missing":
            s.pop(k)
        else:
            s[k] = 1.5
        assert m.compare(_bq_out(tmp_path, s, warning=False)) == 1
    assert capsys.readouterr().out.count("::error") == 3


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    s = m.summarise(m.local_scores())
    assert {k: result[k] for k in s} == s


def test_cloud_script_redacts_and_creates_nothing():
    text = (FEATURE / "cloud.sh").read_text()
    assert "s/[0-9]{6,}/<number>/g" in text and "--maximum_bytes_billed=20971520" in text
    assert "CREATE" not in m.sql() and "bq mk" not in text
