"""Day 43 Google Cloud: the hand-written two-page terms PDF, the answer key, the AI.CHUNK_DOCUMENT SQL and the
cloud-output check (chunks at two sizes, a warning line before the JSON, error columns, missing terms, wrong pages,
split terms)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-43-supplier-terms-chunk"
spec = importlib.util.spec_from_file_location("terms43", FEATURE / "supplier_terms.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)


def test_pdf_is_deterministic_and_committed():
    data = st.pdf_bytes()
    assert data == st.pdf_bytes() == (FEATURE / "data/harbor_farms_terms.pdf").read_bytes()
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF") and b"/Count 2" in data
    xref = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    assert data[xref:xref + 4] == b"xref"
    for i, e in enumerate(data[xref:].split(b"\n")[3:12], 1):
        off = int(e[:10])
        assert data[off:off + len(f"{i} 0 obj")] == f"{i} 0 obj".encode()


def test_every_term_is_printed_on_its_page():
    pages = st.pdf_pages_text(st.pdf_bytes())
    assert len(pages) == 2 and pages[0].startswith(st.TITLE)
    for term, info in st.answer_key().items():
        assert term in pages[info["page"] - 1] and term not in pages[2 - info["page"]]
    assert set(st.QUESTIONS.values()) == set(st.answer_key())


def test_result_json_matches():
    saved = json.loads((FEATURE / "result.json").read_text())
    s = st.summarise()
    assert {k: saved[k] for k in s} == s
    assert saved["terms"] == 8 and saved["chunk_sizes"] == [100, 500]


def test_sql_shape():
    q = st.sql("gs://mf-day43-1-2-3/terms/harbor_farms_terms.pdf")
    assert q.count("AI.CHUNK_DOCUMENT(") == 2 and "chunk_size => 100" in q and "chunk_size => 500" in q
    assert "UNION ALL" in q and "connection_id" not in q
    assert q.lstrip().startswith("--")  # starts with a comment: SQL must go in on stdin
    with pytest.raises(ValueError):
        st.sql("gs://x'; DROP TABLE t; --/a.pdf")


def _paragraphs():
    return [(p, f"{h} {t}") for p, h, t, _ in st.SECTIONS]


def _rows(chunks_by_size):
    rows = []
    for n, chunks in chunks_by_size.items():
        for i, (s, e, text) in enumerate(chunks):
            rows.append({"requested_size": str(n), "chunk_id": str(i), "start_page": str(s), "end_page": str(e),
                         "content": text})
    return rows


def _good():
    paras = _paragraphs()
    small = [(p, p, t) for p, t in paras]  # one chunk per section
    large = [(1, 1, " ".join(t for p, t in paras if p == 1)), (2, 2, " ".join(t for p, t in paras if p == 2))]
    return {100: small, 500: large}


def _out(tmp_path, rows, prefix=""):
    p = tmp_path / "out.json"
    p.write_text(prefix + json.dumps(rows))
    return str(p)


def test_compare_good(tmp_path, capsys):
    assert st.compare(_out(tmp_path, _rows(_good()), prefix="WARNING: --scopes\n")) == 0
    out = capsys.readouterr().out
    assert "chunk_size 100: 8 chunks" in out and "chunk_size 500: 2 chunks" in out and "8/8 terms whole" in out
    assert "::warning" not in out


def test_compare_split_term_warns(tmp_path, capsys):
    g = _good()
    p, _, text = g[100][5]  # "41 F or below" split across two chunks
    i = text.index("41 F or") + len("41 F")
    g[100][5:6] = [(p, p, text[:i]), (p, p, text[i:])]
    assert st.compare(_out(tmp_path, _rows(g))) == 0
    out = capsys.readouterr().out
    assert "7/8 terms whole" in out and "::warning" in out and "41 F or below" in out.split("::warning")[1]


def test_compare_failures(tmp_path, capsys):
    good = _rows(_good())
    assert st.compare(_out(tmp_path, [r for r in good if r["requested_size"] == "100"])) == 1
    assert "no chunks for chunk_size 500" in capsys.readouterr().out
    assert st.compare(_out(tmp_path, good[:1] + [{**good[1], "ml_generate_error": "permission denied"}]
                           + good[2:])) == 1
    assert "with errors" in capsys.readouterr().out
    assert st.compare(_out(tmp_path, [{**r, "content": r["content"].replace("net 30", "net 60")
                                       .replace("take a 2%", "take a 3%")} for r in good])) == 1
    assert "terms missing: take a 2% discount" in capsys.readouterr().out
    assert st.compare(_out(tmp_path, good + [{**good[0], "start_page": "3", "end_page": "3"}])) == 1
    assert "expected [1, 2]" in capsys.readouterr().out
    g = _good()
    g[100] = g[500][:1] + g[500][1:]  # small size no finer than large: fine (equal)
    assert st.compare(_out(tmp_path, _rows(g))) == 0
    g[500] = _good()[100]
    g[100] = _good()[500]
    assert st.compare(_out(tmp_path, _rows(g))) == 1
    assert "fewer chunks" in capsys.readouterr().out
    p = tmp_path / "bad.json"
    p.write_text("not json [")
    assert st.compare(str(p)) == 1


def test_ok_status_column_is_not_an_error(tmp_path):
    assert st.compare(_out(tmp_path, [{**r, "status": "OK"} for r in _rows(_good())])) == 0
