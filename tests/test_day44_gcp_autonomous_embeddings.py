"""Day 44 Google Cloud: the answer key, the word-overlap baseline in result.json, the generated-column DDL (no
connection, asynchronous, nothing written to the embedding column), the status poll and the search check (no cloud)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-44-aisle-finder-embeddings"
spec = importlib.util.spec_from_file_location("aisle_finder44", FEATURE / "aisle_finder.py")
af = importlib.util.module_from_spec(spec)
spec.loader.exec_module(af)


def test_result_json_matches():
    saved = json.loads((FEATURE / "result.json").read_text())
    s = af.summarise()
    assert {k: saved[k] for k in s} == s
    assert s["products_total"] == 12 and s["queries"] == 9
    assert s["lexical_correct"] <= 2  # the queries are phrased the way shoppers talk, not like the shelf tags


def test_answer_key_points_at_real_products():
    skus = {p[0] for p in af.PRODUCTS + [af.FRESH]}
    assert all(sku in skus for _, _, sku in af.QUERIES)
    assert dict((q, s) for q, _, s in af.QUERIES)[af.FRESH_QID] == af.FRESH[0]
    assert af.FRESH[0] not in {p[0] for p in af.PRODUCTS}


def test_setup_sql_declares_autonomous_column():
    q = af.bigquery_sql("setup", "mf_day44_1")
    assert "GENERATED ALWAYS AS (AI.EMBED(description, model => 'embeddinggemma-300m'))" in q
    assert "asynchronous = TRUE" in q and "connection_id" not in q
    assert "(sku, name, aisle, description)" in q and q.count("),\n") == len(af.PRODUCTS) - 1
    assert "cow\\'s milk" in q
    assert q.lstrip().startswith("--")
    fresh = af.bigquery_sql("fresh", "mf_day44_1")
    assert "DA-1005" in fresh and "description_embedding" not in fresh
    with pytest.raises(ValueError):
        af.bigquery_sql("setup", "x`; DROP")


def test_search_sql():
    q = af.bigquery_sql("search", "mf_day44_1")
    assert q.count("AI.SEARCH(TABLE `mf_day44_1.products`, 'description'") == len(af.QUERIES)
    fb = af.bigquery_sql("search_fallback", "mf_day44_1")
    assert "ML.DISTANCE(p.description_embedding.result" in fb and "model => 'embeddinggemma-300m'" in fb


def test_status():
    ok = {"total": "11", "embedded": "11", "errored": "0", "dims_min": "768", "dims_max": "768"}
    assert af.check_status(ok, 11)[0] == "done"
    assert af.check_status({**ok, "embedded": "4"}, 11)[0] == "waiting"
    assert af.check_status({**ok, "errored": "1", "errors": "quota"}, 11)[0] == "failed"
    assert af.check_status({**ok, "dims_min": "512"}, 11)[0] == "failed"
    assert af.check_status(ok, 12)[0] == "failed"


def _rows(top):
    return [{"qid": q, "sku": s, "distance": str(0.1 * (i + 1)), "rnk": str(i + 1)}
            for q, skus in top.items() for i, s in enumerate(skus)]


def test_evaluate(tmp_path):
    perfect = {q: [sku, "PN-6002", "HF-1001"] for q, _, sku in af.QUERIES}
    e = af.evaluate(_rows(perfect))
    assert e["errors"] == [] and e["correct"] == 9 and e["fresh_rank"] == 1
    worse = dict(perfect, q9=["HF-1001", "DA-1005", "PN-6002"])
    e = af.evaluate(_rows(worse))
    assert e["errors"] == [] and e["correct"] == 8 and e["fresh_rank"] == 2
    e = af.evaluate(_rows(dict(perfect, q9=["HF-1001", "PN-6002", "PN-6001"])))
    assert any("late-arriving" in x for x in e["errors"])
    bad = {q: ["PN-6002"] for q, _, _ in af.QUERIES}
    assert any("only 0/9" in x for x in af.evaluate(_rows(bad))["errors"])
    p = tmp_path / "s.json"
    p.write_text("WARNING: scopes\n" + json.dumps(_rows(perfect)))
    assert af.main(["--compare", str(p)]) == 0
