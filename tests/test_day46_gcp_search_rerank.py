"""Day 46 Google Cloud: AI.SEARCH with the rerank option over an autonomously embedded catalog. The answer key in
result.json, the SQL for every call form, parsing output rows of different shapes, the check, and cloud.sh (embedding
wait, call-form probing) against a fake `bq` / `gcloud` (no cloud)."""
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-46-aisle-search-rerank"
spec = importlib.util.spec_from_file_location("aisle_search_rerank46", FEATURE / "aisle_search_rerank.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)


def test_result_json_matches():
    saved = json.loads((FEATURE / "result.json").read_text())
    s = sr.summarise()
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))
    assert s["products"] == 12 and s["queries"] == 9 and s["top_k"] == 5 and s["min_correct"] == 7


def test_answer_key_points_at_real_products():
    skus = {p[0] for p in sr.PRODUCTS}
    assert all(sku in skus for _, _, sku in sr.QUERIES) and len(skus) == len(sr.PRODUCTS)


def test_sql():
    setup = sr.bigquery_sql("setup", "mf_day46_1")
    assert "GENERATED ALWAYS AS (AI.EMBED(description, model => 'embeddinggemma-300m'))" in setup
    assert "asynchronous = TRUE" in setup and setup.count("),\n") == len(sr.PRODUCTS) - 1 and "cow\\'s milk" in setup
    assert setup.lstrip().startswith("--") and "description_embedding" in sr.bigquery_sql("status", "d")
    for n in sr.FORMS:
        probe = sr.bigquery_sql(f"probe:{n}", "mf_day46_1")
        assert probe.count("AI.SEARCH(") == 1 and "'pasta sauce'" in probe and "rerank" in probe
        search = sr.bigquery_sql(f"search:{n}", "mf_day46_1")
        assert search.count("AI.SEARCH(") == 9 and search.count("UNION ALL") == 8
        assert str(n) in sr.FORM_LABELS
    assert ("AI.SEARCH(TABLE `mf_day46_1.products`, 'description', 'fresh bread', top_k => 5, rerank => TRUE)"
            in sr.bigquery_sql("search:1", "mf_day46_1"))
    assert """options => JSON '{"rerank": true}'""" in sr.bigquery_sql("probe:3", "d")
    with pytest.raises(ValueError):
        sr.bigquery_sql("setup", "x`; DROP")


def test_parse_row_shapes():
    r = sr.parse_row(json.dumps({"base": {"sku": "PN-6003", "description_embedding": {"result": [0.1, 0.2],
                                 "status": ""}}, "distance": 0.4, "relevance_score": 0.93}))
    assert (r["sku"], r["rank"], r["score"], r["distance"]) == ("PN-6003", None, 0.93, 0.4)
    assert r["score_col"] == "relevance_score"
    r = sr.parse_row(json.dumps({"base": {"sku": "PN-6002"}, "distance": "0.3", "rerank_rank": 2}))
    assert (r["rank"], r["score"]) == (2.0, None)
    assert sr.parse_row(json.dumps({"description": "x"}))["sku"] is None


def _rows(top, shape="score"):
    """Rerank order = list order; vector distance order is the reverse, so every top pick was reordered."""
    out = []
    for q, skus in top.items():
        for i, s in enumerate(skus):
            row = {"base": {"sku": s}, "distance": 0.1 * (len(skus) - i)}
            row.update({"relevance_score": 1 - i / 20} if shape == "score" else {"rerank_rank": i + 1})
            out.append({"qid": q, "j": json.dumps(row)})
    return out


def _perfect():
    others = ["PN-6002", "HF-1001", "HF-2002", "BK-5001"]
    return {q: [sku] + [o for o in others if o != sku][:4] for q, _, sku in sr.QUERIES}


def test_evaluate():
    e = sr.evaluate(_rows(_perfect()))
    assert e["errors"] == [] and e["correct"] == 9 and e["answer_in_candidates"] == 9
    assert e["vector_top1_correct"] == 0 and len(e["reordered_by_rerank"]) == 9
    assert e["columns"]["score"] == "relevance_score"
    e = sr.evaluate(_rows(_perfect(), shape="rank"))
    assert e["errors"] == [] and e["correct"] == 9 and e["columns"]["rank"] == "rerank_rank"
    worse = dict(_perfect(), q9=["HF-1001", "DA-1005", "PN-6002"], q6=["PN-6002", "HF-1001", "HF-2002"])
    e = sr.evaluate(_rows(worse))
    assert e["errors"] == [] and e["correct"] == 7 and e["answer_in_candidates"] == 8
    worse["q1"] = ["PN-6002", "PN-6001", "HF-1001"]
    assert any("only 6/9" in x for x in sr.evaluate(_rows(worse))["errors"])
    short = {q: v[:2] for q, v in _perfect().items()}
    assert any("fewer than 3" in x for x in sr.evaluate(short and _rows(short))["errors"])
    no_score = [{"qid": q, "j": json.dumps({"base": {"sku": s}, "distance": 0.2})} for q, v in _perfect().items()
                for s in v]
    assert any("no rerank score or rank column" in x for x in sr.evaluate(no_score)["errors"])


def test_status():
    assert sr.check_status({"total": 12, "embedded": 12, "errored": 0}, 12)[0] == "done"
    assert sr.check_status({"total": 12, "embedded": 5, "errored": 0}, 12)[0] == "waiting"
    assert sr.check_status({"total": 12, "embedded": 5, "errored": 1, "errors": "quota"}, 12)[0] == "failed"


FAKE_BQ = r"""#!/usr/bin/env bash
# fake bq: accepts mk / rm; status says all embedded; AI.SEARCH forms in $REFUSE are refused
for a in "$@"; do case "$a" in mk|rm) exit 0;; esac; done
sql=$(cat)
echo "$sql" >> "$LOG"
case "$sql" in
  *"progress of the background"*) echo '[{"total": "12", "embedded": "12", "errored": "0", "errors": null}]'; exit 0;;
  *"AI.SEARCH"*) ;;
  *) echo "[]"; exit 0;;
esac
for f in $REFUSE; do
  case "$f:$sql" in
    1:*"rerank => TRUE)"*) case "$sql" in *top_k*) echo "Error: Named argument rerank not found for AI.SEARCH in project 123456789" >&2; exit 1;; esac;;
    2:*"semantic-ranker"*) echo "Error: form 2 refused" >&2; exit 1;;
    3:*"options => JSON"*) echo "Error: form 3 refused" >&2; exit 1;;
    4:*"rerank => TRUE)"*) echo "Error: form 4 refused" >&2; exit 1;;
  esac
done
echo "WARNING: --scopes"
python3 "$ROWS" "$sql"
"""

FAKE_GCLOUD = r"""#!/usr/bin/env bash
echo "gcloud $*" >> "$LOG"
case "$*" in
  "services list"*) [ -z "$API_OFF" ] && echo "discoveryengine.googleapis.com"; exit 0;;
  "services enable"*) exit 0;;
esac
exit 1
"""

ROWS = r"""import json, re, sys
sql = sys.argv[1]
expected = {"q1": "PN-6001", "q2": "HH-7001", "q3": "PR-4002", "q4": "FZ-8001", "q5": "PR-4001",
            "q6": "PN-6003", "q7": "BK-5001", "q8": "HF-3001", "q9": "DA-1005"}
out = []
for qid in re.findall(r"SELECT '(q\d)' AS qid", sql):
    skus = [expected[qid]] + [s for s in ("PN-6002", "HF-1001", "HF-2002", "BK-5001") if s != expected[qid]][:4]
    # vector distance puts the expected product second for q6 and q9 (day 44's misses); rerank puts it first
    dist = {s: 0.2 + 0.05 * i for i, s in enumerate(skus)}
    if qid in ("q6", "q9"):
        dist[skus[0]], dist[skus[1]] = dist[skus[1]], dist[skus[0]]
    out += [{"qid": qid, "j": json.dumps({"base": {"sku": s, "description_embedding": {"result": [0.1], "status": ""}},
                                          "distance": dist[s], "relevance_score": round(0.9 - i * 0.1, 2)})}
            for i, s in enumerate(skus)]
print(json.dumps(out))
"""


def _run_cloud(tmp_path, refuse, api_off=""):
    if not shutil.which("bash"):
        pytest.skip("bash needed")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in (("bq", FAKE_BQ), ("gcloud", FAKE_GCLOUD), ("sleep", "#!/bin/sh\nexit 0\n")):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    (tmp_path / "rows.py").write_text(ROWS)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "GCP_PROJECT_ID": "my-proj",
           "REFUSE": refuse, "API_OFF": api_off, "LOG": str(tmp_path / "log.sql"), "ROWS": str(tmp_path / "rows.py")}
    return subprocess.run(["bash", "cloud.sh"], cwd=FEATURE, env=env, capture_output=True, text=True, timeout=120)


def test_cloud_sh_uses_first_accepted_form(tmp_path):
    p = _run_cloud(tmp_path, "1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "background embedding finished" in p.stdout and "12/12 embedded" in p.stdout
    assert "form 2 accepted; earlier forms refused: [form 1]" in p.stdout and "123456789" not in p.stdout
    assert "::notice title=Google Cloud day 46::AI.SEARCH with rerank (call form 2" in p.stdout
    assert "9/9 shopper queries" in p.stdout and "ordered by relevance_score" in p.stdout
    assert "vector distance alone 7/9" in p.stdout and "changed the top pick for 2 queries" in p.stdout
    assert "my-proj" not in p.stdout


def test_cloud_sh_reports_every_refused_form(tmp_path):
    p = _run_cloud(tmp_path, "1 2 3 4")
    assert p.returncode == 1
    assert "::error title=Day 46 AI.SEARCH rerank refused in every call form::" in p.stdout
    assert "[form 1]" in p.stdout and "form 4 refused" in p.stdout and "123456789" not in p.stdout


def test_cloud_sh_enables_the_ranking_api_if_off(tmp_path):
    p = _run_cloud(tmp_path, "", api_off="1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "enabled the Discovery Engine API" in p.stdout and "call form 1" in p.stdout
    p = _run_cloud(tmp_path, "")
    assert "enabled the Discovery Engine API" not in p.stdout
