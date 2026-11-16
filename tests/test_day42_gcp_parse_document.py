"""Day 42 Google Cloud: the hand-written supplier PDF, the answer key, the AI.PARSE_DOCUMENT SQL and the cloud-output
check (table block or plain text blocks, a warning line before the JSON, parse errors, missing values)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-42-supplier-sheet-parse"
spec = importlib.util.spec_from_file_location("supplier42", FEATURE / "supplier_sheet.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def test_pdf_is_deterministic_and_committed():
    data = ss.pdf_bytes()
    assert data == ss.pdf_bytes() == (FEATURE / "data/harbor_farms_w46.pdf").read_bytes()
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    # xref offsets point at the objects
    xref = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    assert data[xref:xref + 4] == b"xref"
    entries = data[xref:].split(b"\n")[3:10]
    for i, e in enumerate(entries, 1):
        off = int(e[:10])
        assert data[off:off + len(f"{i} 0 obj")] == f"{i} 0 obj".encode()


def test_pdf_text_holds_every_row():
    text = ss.pdf_text(ss.pdf_bytes())
    assert text[0] == ss.TITLE and ss.HEADING in text
    for sku, product, units, size, cost in ss.ROWS:
        i = text.index(sku)
        assert text[i:i + 4] == [sku, product, f"{units} x {size}", cost]


def test_answer_key_and_result_json():
    key = ss.answer_key()
    assert key["HF-2002"]["unit_cost"] == "3.49" and key["HF-3001"]["unit_cost"] == "2.49"
    saved = json.loads((FEATURE / "result.json").read_text())
    s = ss.summarise()
    assert {k: saved[k] for k in s} == s
    assert saved["case_cost_total"] == "160.75" and saved["lowest_unit_cost"] == "HF-3001"


def test_sql_shape():
    q = ss.sql("gs://mf-day42-1-2-3/supplier/harbor_farms_w46.pdf")
    assert q.count("AI.PARSE_DOCUMENT(") == 1 and "'layout-v1'" not in q and '"schema": "layout-v1"' in q
    assert "connection_id" not in q.split("\n", 2)[2]  # user credentials, no connection
    assert not q.lstrip().startswith("WITH")  # starts with a comment: SQL must go in on stdin
    with pytest.raises(ValueError):
        ss.sql("gs://x'; DROP TABLE t; --/a.pdf")


def _table_html():
    head = "<tr>" + "".join(f"<th>{c}</th>" for c in ss.COLUMNS) + "</tr>"
    body = "".join(f"<tr><td>{s}</td><td>{p}</td><td>{u} x {z}</td><td>{c}</td></tr>" for s, p, u, z, c in ss.ROWS)
    return f"<table>{head}{body}</table>".replace("2%", "2&#37;")


def _out(tmp_path, blocks, errors="null", schema="layout-v1", prefix=""):
    rows = [{"schema_used": schema, "errors": errors, "block_count": str(len(blocks)), "blocks": json.dumps(blocks)}]
    p = tmp_path / "out.json"
    p.write_text(prefix + json.dumps(rows))
    return str(p)


def _head():
    return [{"id": 0, "type": "title", "page": 1, "content": ss.TITLE},
            {"id": 1, "type": "heading", "page": 1, "content": ss.HEADING},
            {"id": 2, "type": "text", "page": 1, "content": ss.NOTE}]


def test_compare_table(tmp_path, capsys):
    blocks = _head() + [{"id": 3, "type": "table", "page": 1, "content": _table_html()}]
    assert ss.compare(_out(tmp_path, blocks, prefix="WARNING: --scopes\n")) == 0
    out = capsys.readouterr().out
    assert "an HTML table came back" in out and "6/6 SKU rows rebuilt" in out and "::warning" not in out


def test_compare_text_rows(tmp_path, capsys):
    blocks = _head() + [{"id": 3 + i, "type": "text", "page": "1", "content": f"{s} {p} {u} x {z} {c}"}
                        for i, (s, p, u, z, c) in enumerate(ss.ROWS)]
    assert ss.compare(_out(tmp_path, blocks)) == 0
    assert "6/6 SKU rows rebuilt" in capsys.readouterr().out


def test_compare_column_order_warns(tmp_path, capsys):
    cols = list(zip(*ss.ROWS))
    blocks = _head() + [{"id": 9, "type": "text", "page": 1, "content": " ".join(map(str, col))} for col in cols]
    assert ss.compare(_out(tmp_path, blocks)) == 0
    out = capsys.readouterr().out
    assert "::warning" in out and "/6 SKU rows rebuilt" in out


def test_compare_failures(tmp_path, capsys):
    good = _head() + [{"id": 3, "type": "table", "page": 1, "content": _table_html()}]
    assert ss.compare(_out(tmp_path, [], errors='[{"code":7,"message":"permission denied"}]')) == 1
    assert "parse errors" in capsys.readouterr().out
    assert ss.compare(_out(tmp_path, good, schema="raw-docai")) == 1
    assert ss.compare(_out(tmp_path, [b for b in good if b["type"] != "title"])) == 1
    assert "title not found" in capsys.readouterr().out
    assert ss.compare(_out(tmp_path, good[:3] + [{**good[3], "content": _table_html().replace("41.88", "41.38")}])) == 1
    assert "missing from the parse: 41.88" in capsys.readouterr().out
    assert ss.compare(_out(tmp_path, good[:3] + [{**good[3], "page": 2}])) == 1
    p = tmp_path / "bad.json"
    p.write_text("not json [")
    assert ss.compare(str(p)) == 1
