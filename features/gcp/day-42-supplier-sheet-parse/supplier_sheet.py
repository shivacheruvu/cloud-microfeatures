"""Day 42 (Google Cloud): read a supplier's PDF price sheet into rows with BigQuery's AI.PARSE_DOCUMENT
(GA 2026-11-02, Document AI layout parser behind one SQL function), and check the case costs it recovers.

  python supplier_sheet.py                # local: writes data/harbor_farms_w46.pdf + result.json (the answer key)
  python supplier_sheet.py --sql URI      # print the BigQuery SQL for the PDF at gs://... URI
  python supplier_sheet.py --compare OUT  # check bq's JSON output against the answer key (cloud.sh)

The job: a dairy supplier emails a one-page PDF each week with its case costs (SKU, product, case pack, case cost).
Somebody retypes it into the cost file. AI.PARSE_DOCUMENT returns the page as layout blocks (title, heading, text,
table as HTML ...) in reading order, so the price list becomes rows in SQL.

The PDF is written here byte for byte (no PDF library): Helvetica text and a ruled grid, so its content is known.
Must hold (else the run fails): schema layout-v1, no errors, at least one block, every block on page 1, the sheet's
title found, and every SKU and every case cost present in the parsed text. Reported: block types, whether a table
block came back, and how many SKU rows were rebuilt with the right case cost (fewer than 6 is a warning: a finding
about the parser's layout, not a broken run).
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PDF_PATH = HERE / "data" / "harbor_farms_w46.pdf"
TITLE = "Harbor Farms Dairy Co-op"
HEADING = "Case cost price list - week 46, 2026"
NOTE = "Prices in USD per case, FOB warehouse. Valid Nov 16 - Nov 22, 2026."
COLUMNS = ["SKU", "Product", "Case pack", "Case cost"]
ROWS = [  # sku, product, units per case, unit size, case cost (USD)
    ("HF-1001", "Whole milk", 4, "1 gal", "13.96"),
    ("HF-1002", "Reduced fat milk 2%", 4, "1 gal", "13.56"),
    ("HF-2001", "Greek yogurt plain", 6, "32 oz", "23.40"),
    ("HF-2002", "Salted butter", 12, "1 lb", "41.88"),
    ("HF-3001", "Large eggs grade A", 15, "12 ct", "37.35"),
    ("HF-3002", "Sharp cheddar block", 12, "8 oz", "30.60"),
]
MONEY = re.compile(r"\b\d{1,4}\.\d{2}\b")


# ---------- the PDF (written by hand so it is deterministic) ----------
def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content() -> bytes:
    ops = ["BT /F2 20 Tf 72 740 Td (" + _esc(TITLE) + ") Tj ET",
           "BT /F2 13 Tf 72 712 Td (" + _esc(HEADING) + ") Tj ET",
           "BT /F1 10 Tf 72 694 Td (" + _esc(NOTE) + ") Tj ET"]
    xs = [72, 162, 362, 452, 540]  # column edges
    top, h = 670, 24
    n = len(ROWS) + 1
    ops.append("0.6 w")
    for i in range(n + 1):  # horizontal rules
        y = top - i * h
        ops.append(f"{xs[0]} {y} m {xs[-1]} {y} l S")
    for x in xs:  # vertical rules
        ops.append(f"{x} {top} m {x} {top - n * h} l S")
    cells = [COLUMNS] + [[s, p, f"{u} x {size}", c] for s, p, u, size, c in ROWS]
    for r, row in enumerate(cells):
        y = top - r * h - 16
        font = "/F2" if r == 0 else "/F1"
        for c, text in enumerate(row):
            ops.append(f"BT {font} 11 Tf {xs[c] + 6} {y} Td (" + _esc(text) + ") Tj ET")
    ops.append("BT /F1 9 Tf 72 " + str(top - n * h - 24) + " Td (Orders: 6 cases minimum per SKU. Questions: "
               "pricing desk.) Tj ET")
    return ("\n".join(ops) + "\n").encode("latin-1")


def pdf_bytes() -> bytes:
    content = _content()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Title (" + _esc(TITLE + " price list week 46").encode() + b") /Producer (cloud-microfeatures) >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 7 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def pdf_text(data: bytes) -> list[str]:
    """The strings drawn on the page, in drawing order (only for PDFs written by pdf_bytes)."""
    m = re.search(rb"stream\n(.*?)endstream", data, re.S)
    raw = re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", m.group(1))
    return [re.sub(rb"\\(.)", rb"\1", s).decode("latin-1") for s in raw]


# ---------- the answer key ----------
def answer_key() -> dict:
    return {s: {"product": p, "case_pack": f"{u} x {size}", "case_cost": c,
                "unit_cost": f"{int(round(float(c) * 100 / u)) / 100:.2f}"} for s, p, u, size, c in ROWS}


def summarise() -> dict:
    key = answer_key()
    data = pdf_bytes()
    return {"pages": 1, "pdf_bytes": len(data), "skus": len(key),
            "case_cost_total": f"{sum(float(v['case_cost']) for v in key.values()):.2f}",
            "lowest_unit_cost": min(key, key=lambda s: float(key[s]["unit_cost"])),
            "answer_key": key, "queries": 1, "document_ai_pages": 1}


# ---------- BigQuery ----------
def sql(uri: str) -> str:
    if not re.fullmatch(r"gs://[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]/[A-Za-z0-9._/-]+\.pdf", uri):
        raise ValueError("expected a gs://bucket/path.pdf URI")
    return f"""-- Day 42: parse a supplier PDF price sheet with AI.PARSE_DOCUMENT (GA 2026-11-02), layout-v1 blocks.
-- No connection: the caller's credentials read the object and call the Document AI layout parser.
WITH parsed AS (
  SELECT AI.PARSE_DOCUMENT('{uri}', options => JSON '{{"schema": "layout-v1"}}') AS doc
)
SELECT JSON_VALUE(doc, '$.metadata.schema') AS schema_used,
       TO_JSON_STRING(JSON_QUERY(doc, '$.errors')) AS errors,
       ARRAY_LENGTH(JSON_QUERY_ARRAY(doc, '$.blocks')) AS block_count,
       TO_JSON_STRING(ARRAY(
         SELECT AS STRUCT INT64(b.id) AS id, JSON_VALUE(b.type) AS type, INT64(b.page_number) AS page,
                JSON_VALUE(b.content) AS content
         FROM UNNEST(JSON_QUERY_ARRAY(doc, '$.blocks')) AS b WITH OFFSET AS o
         ORDER BY o)) AS blocks
FROM parsed
"""


def _plain(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def rebuild_rows(blocks: list[dict]) -> dict:
    """SKU -> case cost read from the parse: from a table row when the parser returned an HTML table, else from the
    text that follows the SKU (cells read row by row)."""
    found: dict[str, str] = {}
    for b in blocks:
        if b.get("type") == "table":
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", b.get("content") or "", re.S | re.I):
                cells = [_plain(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
                sku = next((c for c in cells if c in dict.fromkeys(s for s, *_ in ROWS)), None)
                money = [c for c in cells if MONEY.fullmatch(c.replace("$", "").strip())]
                if sku and money and sku not in found:
                    found[sku] = money[-1].replace("$", "").strip()
    text = " ".join(_plain(b.get("content")) for b in blocks if b.get("type") != "table")
    for sku, *_ in ROWS:
        if sku in found:
            continue
        m = re.search(re.escape(sku) + r"(.{0,80}?)(?=HF-\d{4}|$)", text)
        money = MONEY.findall(m.group(1)) if m else []
        if money:
            found[sku] = money[-1]
    return found


def check(rows: list[dict]) -> tuple[list[str], dict]:
    if len(rows) != 1:
        return [f"expected 1 row, got {len(rows)}"], {}
    r = rows[0]
    problems = []
    errors = r.get("errors")
    if errors not in (None, "null", "[]", ""):
        problems.append(f"parse errors: {str(errors)[:300]}")
    if r.get("schema_used") != "layout-v1":
        problems.append(f"schema {r.get('schema_used')!r}, expected layout-v1")
    try:
        blocks = json.loads(r.get("blocks") or "[]")
    except ValueError:
        return problems + ["blocks column is not JSON"], {}
    if not blocks:
        problems.append("no blocks returned")
    pages = sorted({str(b.get("page")) for b in blocks})
    if blocks and pages != ["1"]:
        problems.append(f"blocks on pages {pages}, expected only page 1")
    alltext = " ".join(_plain(b.get("content")) for b in blocks)
    if TITLE.lower() not in alltext.lower():
        problems.append("sheet title not found")
    missing = [s for s, *_ in ROWS if s not in alltext] + [c for *_, c in ROWS if c not in alltext]
    if missing:
        problems.append("missing from the parse: " + ", ".join(missing))
    types: dict[str, int] = {}
    for b in blocks:
        types[b.get("type")] = types.get(b.get("type"), 0) + 1
    rebuilt = rebuild_rows(blocks)
    key = answer_key()
    right = sorted(s for s in key if rebuilt.get(s) == key[s]["case_cost"])
    return problems, {"blocks": len(blocks), "types": types, "table": "table" in types, "rows_right": right,
                      "rows_wrong": {s: rebuilt.get(s) for s in key if s not in right}}


def compare(out_path: str) -> int:
    text = Path(out_path).read_text()
    start = text.find("[")  # bq may print a --scopes warning before the JSON
    try:
        rows = json.loads(text[start:]) if start >= 0 else []
    except ValueError as e:
        print(f"::error title=Unreadable BigQuery output::{e}")
        return 1
    problems, got = check(rows)
    if problems:
        print("::error title=Google Cloud day 42 parse check failed::" + "; ".join(problems)[:900])
        return 1
    n = len(ROWS)
    types = ", ".join(f"{k} {v}" for k, v in sorted(got["types"].items()))
    print(f"::notice title=Google Cloud day 42::AI.PARSE_DOCUMENT read the 1-page supplier price sheet into "
          f"{got['blocks']} layout-v1 blocks ({types}); title, all {n} SKUs and all {n} case costs found; "
          f"{'an HTML table came back; ' if got['table'] else 'no table block; '}"
          f"{len(got['rows_right'])}/{n} SKU rows rebuilt with the right case cost")
    if len(got["rows_right"]) < n:
        print("::warning title=Google Cloud day 42::rows not rebuilt from the layout: "
              + ", ".join(f"{s} -> {v}" for s, v in got["rows_wrong"].items()))
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql(sys.argv[2]))
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2]))
    else:
        PDF_PATH.parent.mkdir(exist_ok=True)
        PDF_PATH.write_bytes(pdf_bytes())
        s = summarise()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "answer key written with the PDF (local) / AI.PARSE_DOCUMENT layout-v1 (cloud)",
             "new_feature": "AI.PARSE_DOCUMENT", **s}, indent=2) + "\n")
        print(json.dumps({k: v for k, v in s.items() if k != "answer_key"}, indent=2))
