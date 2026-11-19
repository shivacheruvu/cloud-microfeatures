"""Day 45 Google Cloud: AI.RANK over the day-44 catalog. The answer key and baseline in result.json, the SQL for every
call form, parsing AI.RANK output rows of different shapes, the check, and cloud.sh's call-form probing against a fake
`bq` (no cloud)."""
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-45-aisle-rerank"
spec = importlib.util.spec_from_file_location("aisle_rerank45", FEATURE / "aisle_rerank.py")
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)


def test_result_json_matches():
    saved = json.loads((FEATURE / "result.json").read_text())
    s = ar.summarise()
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))
    assert s["products"] == 12 and s["queries"] == 9 and s["lexical_correct"] == 1
    assert s["day44_embedding_search"]["correct"] == 7


def test_answer_key_points_at_real_products():
    skus = {p[0] for p in ar.PRODUCTS}
    assert all(sku in skus for _, _, sku in ar.QUERIES) and len(skus) == len(ar.PRODUCTS)
    assert set(ar.DAY44["misses"]) <= {q for q, _, _ in ar.QUERIES}


def test_sql():
    setup = ar.bigquery_sql("setup", "mf_day45_1")
    assert "AI.EMBED" not in setup and "GENERATED" not in setup and setup.count("),\n") == len(ar.PRODUCTS) - 1 and "cow\\'s milk" in setup
    assert setup.lstrip().startswith("--")
    for n in ar.FORMS:
        probe = ar.bigquery_sql(f"probe:{n}", "mf_day45_1")
        assert probe.count("AI.RANK(") == 1 and "'pasta sauce'" in probe and "TO_JSON_STRING(r)" in probe
        rank = ar.bigquery_sql(f"rank:{n}", "mf_day45_1")
        assert rank.count("AI.RANK(") == 9 and rank.count("UNION ALL") == 8
        assert str(n) in ar.FORM_LABELS
    assert "AI.RANK(TABLE `mf_day45_1.products`, 'description', 'fresh bread')" in ar.bigquery_sql("rank:1", "mf_day45_1")
    with pytest.raises(ValueError):
        ar.bigquery_sql("setup", "x`; DROP")


def test_parse_row_shapes():
    flat = ar.parse_row(json.dumps({"sku": "PN-6003", "name": "Marinara", "relevance_score": 0.93, "rank": 1}))
    assert (flat["sku"], flat["rank"], flat["score"]) == ("PN-6003", 1.0, 0.93)
    nested = ar.parse_row(json.dumps({"base": {"sku": "PN-6002", "description": "x"}, "score": "0.4"}))
    assert (nested["sku"], nested["rank"], nested["score"]) == ("PN-6002", None, 0.4)
    assert ar.parse_row(json.dumps({"description": "x"}))["sku"] is None


def _rows(top, shape="rank"):
    out = []
    for q, skus in top.items():
        for i, s in enumerate(skus):
            row = {"sku": s, "rank": i + 1} if shape == "rank" else {"base": {"sku": s}, "score": 1 - i / 20}
            out.append({"qid": q, "j": json.dumps(row)})
    return out


def _perfect():
    others = ["PN-6002", "HF-1001", "HF-2002"]
    return {q: [sku] + [o for o in others if o != sku] for q, _, sku in ar.QUERIES}


def test_evaluate():
    e = ar.evaluate(_rows(_perfect()))
    assert e["errors"] == [] and e["correct"] == 9 and e["fixed_day44_misses"] == ["q6", "q9"]
    assert e["columns"]["rank"] == "rank"
    e = ar.evaluate(_rows(_perfect(), shape="score"))
    assert e["errors"] == [] and e["correct"] == 9 and e["columns"]["score"] == "score"
    worse = dict(_perfect(), q9=["HF-1001", "DA-1005", "PN-6002"], q6=["PN-6002", "PN-6003", "HF-1001"])
    e = ar.evaluate(_rows(worse))
    assert e["errors"] == [] and e["correct"] == 7 and e["fixed_day44_misses"] == []
    worse["q1"] = ["PN-6002", "PN-6001", "HF-1001"]
    assert any("only 6/9" in x for x in ar.evaluate(_rows(worse))["errors"])
    short = {q: v[:2] for q, v in _perfect().items()}
    assert any("fewer than 3" in x for x in ar.evaluate(_rows(short))["errors"])
    no_key = [{"qid": q, "j": json.dumps({"sku": s})} for q, v in _perfect().items() for s in v]
    assert any("neither a rank nor a score" in x for x in ar.evaluate(no_key)["errors"])


FAKE_BQ = r"""#!/usr/bin/env bash
# fake bq: accepts mk / rm; for queries, refuses the forms listed in $REFUSE and answers AI.RANK with ranked rows
for a in "$@"; do case "$a" in mk|rm) exit 0;; esac; done
sql=$(cat)
echo "$sql" >> "$LOG"
case "$sql" in *"AI.RANK"*) ;; *) echo "[]"; exit 0;; esac
for f in $REFUSE; do
  case "$f:$sql" in
    1:*"AI.RANK(TABLE"*) case "$sql" in *top_k*) ;; *) echo "Error in query string: No matching signature for AI.RANK in project 123456789" >&2; exit 1;; esac;;
    2:*"AI.RANK((SELECT"*) echo "Error: form 2 refused" >&2; exit 1;;
    3:*"top_k"*) echo "Error: form 3 refused" >&2; exit 1;;
  esac
done
echo "WARNING: --scopes"
python3 "$ROWS" "$sql"
"""

FAKE_GCLOUD = r"""#!/usr/bin/env bash
# fake gcloud: the Discovery Engine API is enabled unless $API_OFF is set; `services enable` succeeds
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
    skus = [expected[qid]] + [s for s in ("PN-6002", "HF-1001", "HF-2002") if s != expected[qid]]
    out += [{"qid": qid, "j": json.dumps({"sku": s, "relevance_score": round(0.9 - i * 0.1, 2), "rank": i + 1})}
            for i, s in enumerate(skus)]
print(json.dumps(out))
"""


def _run_cloud(tmp_path, refuse, api_off=""):
    if not shutil.which("bash"):
        pytest.skip("bash needed")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "bq").write_text(FAKE_BQ)
    (bindir / "bq").chmod(0o755)
    (bindir / "gcloud").write_text(FAKE_GCLOUD)
    (bindir / "gcloud").chmod(0o755)
    (bindir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    (bindir / "sleep").chmod(0o755)
    (tmp_path / "rows.py").write_text(ROWS)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "GCP_PROJECT_ID": "my-proj",
           "REFUSE": refuse, "API_OFF": api_off, "LOG": str(tmp_path / "log.sql"), "ROWS": str(tmp_path / "rows.py")}
    return subprocess.run(["bash", "cloud.sh"], cwd=FEATURE, env=env, capture_output=True, text=True, timeout=120)


def test_cloud_sh_uses_first_accepted_form(tmp_path):
    p = _run_cloud(tmp_path, "1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "form 2 accepted; earlier forms refused: [form 1]" in p.stdout and "123456789" not in p.stdout
    assert "::notice title=Google Cloud day 45::AI.RANK (call form 2" in p.stdout
    assert "9/9 shopper queries" in p.stdout and "ordered by rank" in p.stdout
    assert "my-proj" not in p.stdout


def test_cloud_sh_reports_every_refused_form(tmp_path):
    p = _run_cloud(tmp_path, "1 2 3")
    assert p.returncode == 1
    assert "::error title=Day 45 AI.RANK refused in every call form::" in p.stdout
    assert "[form 1]" in p.stdout and "form 3 refused" in p.stdout and "123456789" not in p.stdout


def test_cloud_sh_enables_the_ranking_api_once(tmp_path):
    p = _run_cloud(tmp_path, "", api_off="1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "enabled the Discovery Engine API (discoveryengine.googleapis.com) for AI.RANK" in p.stdout
    assert "gcloud services enable discoveryengine.googleapis.com" in (tmp_path / "log.sql").read_text()
    assert "call form 1" in p.stdout
    p = _run_cloud(tmp_path, "")
    assert "enabled the Discovery Engine API" not in p.stdout
