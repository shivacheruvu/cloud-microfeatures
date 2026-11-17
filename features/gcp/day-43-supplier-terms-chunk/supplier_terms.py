"""Day 43 (Google Cloud): split a supplier's two-page terms PDF into search-ready chunks with BigQuery's
AI.CHUNK_DOCUMENT (GA 2026-10-21, a table-valued function backed by the Document AI layout parser), and check that
every term a receiving clerk asks about lands, whole, in a chunk on the right page.

  python supplier_terms.py                # local: writes data/harbor_farms_terms.pdf + result.json (the answer key)
  python supplier_terms.py --sql URI      # print the BigQuery SQL for the PDF at gs://... URI
  python supplier_terms.py --compare OUT  # check bq's JSON output against the answer key (cloud.sh)

The job: day 42 read the supplier's weekly price *table*. The same supplier also sends its trading terms (ordering,
delivery, returns, recalls, cold chain, payment ...) as running text. To answer "how long do we have to return
spoiled yogurt?" with retrieval (embed chunks, search, quote the chunk), the PDF first has to become chunks that keep
each term intact and know which page they came from. AI.CHUNK_DOCUMENT does that in one FROM clause.

The PDF is written here byte for byte (no PDF library), so the eight terms and their pages are known exactly.
One query chunks it twice (chunk_size 100 and 500) so the run also shows what the size knob does.
Must hold (else the run fails): rows come back for both sizes, no chunk reports an error, chunk text is non-empty,
chunks span pages 1 and 2 and nothing else, every term's text appears in the chunks of each size, and the small
size gives at least as many chunks as the large one. Reported: chunk counts, longest chunk, and for each size how
many terms sit whole inside one chunk whose page range holds the term's page (fewer than 8 is a warning: a finding
about the chunker's boundaries, not a broken run).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PDF_PATH = HERE / "data" / "harbor_farms_terms.pdf"
TITLE = "Harbor Farms Dairy Co-op - Trading terms for stores"
SIZES = (100, 500)

# (page, heading, paragraph, term): the term is the exact phrase a clerk's question is answered by.
SECTIONS = [
    (1, "1. Ordering",
     "Stores order through the pricing desk by 3:00 PM the day before delivery. The minimum order is 6 cases per "
     "SKU; mixed pallets are accepted once the store reaches 40 cases in total.",
     "6 cases per SKU"),
    (1, "2. Delivery",
     "Trucks deliver on Tuesday and Friday before 7:00 AM to the receiving dock. A driver waits no more than 20 "
     "minutes; a store that misses its window is moved to the next route.",
     "Tuesday and Friday before 7:00 AM"),
    (1, "3. Prices",
     "Case costs on the weekly price sheet are held for 14 days from the sheet date. Fuel surcharges are listed on "
     "the invoice as their own line and are never added into the case cost.",
     "held for 14 days"),
    (1, "4. Returns",
     "Damaged or short-dated product may be returned within 48 hours of delivery. Keep the product chilled, mark it "
     "with the delivery number and leave it for the next driver to collect.",
     "within 48 hours of delivery"),
    (2, "5. Recalls",
     "On a recall the co-op calls every affected store. Pull the listed lots from the shelf within 2 hours of a "
     "recall notice and record the count on the recall form the driver brings.",
     "within 2 hours of a recall notice"),
    (2, "6. Cold chain",
     "Milk, yogurt, butter and cheese must be received at 41 F or below. Reject any case that reads warmer at the "
     "dock and note the reading on the delivery slip.",
     "41 F or below"),
    (2, "7. Payment",
     "Invoices are due net 30 from the delivery date. Stores that pay within 10 days take a 2% discount; the "
     "discount is shown on the statement the following week.",
     "take a 2% discount"),
    (2, "8. Shortage claims",
     "Report a short or missing case on the delivery slip. The co-op issues a credit memo within 5 business days, "
     "and the credit appears on the next statement.",
     "credit memo within 5 business days"),
]
QUESTIONS = {  # what a clerk asks -> the term that answers it
    "What is the minimum order?": "6 cases per SKU",
    "When do trucks deliver?": "Tuesday and Friday before 7:00 AM",
    "How long is a case cost held?": "held for 14 days",
    "How long do we have to return spoiled yogurt?": "within 48 hours of delivery",
    "How fast must recalled lots come off the shelf?": "within 2 hours of a recall notice",
    "What is the warmest milk we can accept?": "41 F or below",
    "What discount do we get for paying early?": "take a 2% discount",
    "When do we get credit for a short case?": "credit memo within 5 business days",
}
LINE_CHARS = 88
OK_STATUS = {"ok", "success", "succeeded", "done", "completed"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def wrap(text: str, width: int = LINE_CHARS) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    return lines + ([cur] if cur else [])


# ---------- the PDF (written by hand so it is deterministic) ----------
def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _page_ops(page: int) -> bytes:
    ops = []
    y = 740
    if page == 1:
        ops.append("BT /F2 18 Tf 72 740 Td (" + _esc(TITLE) + ") Tj ET")
        ops.append("BT /F1 10 Tf 72 720 Td (" + _esc("Effective November 16, 2026. Applies to every store on the "
                                                     "Harbor Farms route.") + ") Tj ET")
        y = 684
    for p, heading, para, _ in SECTIONS:
        if p != page:
            continue
        ops.append(f"BT /F2 13 Tf 72 {y} Td (" + _esc(heading) + ") Tj ET")
        y -= 20
        for line in wrap(para):
            ops.append(f"BT /F1 11 Tf 72 {y} Td (" + _esc(line) + ") Tj ET")
            y -= 15
        y -= 18
    ops.append(f"BT /F1 9 Tf 72 40 Td (Harbor Farms trading terms - page {page} of 2) Tj ET")
    return ("\n".join(ops) + "\n").encode("latin-1")


def pdf_bytes() -> bytes:
    c1, c2 = _page_ops(1), _page_ops(2)
    res = b"/Resources << /Font << /F1 7 0 R /F2 8 0 R >> >>"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R " + res + b" >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 6 0 R " + res + b" >>",
        b"<< /Length " + str(len(c1)).encode() + b" >>\nstream\n" + c1 + b"endstream",
        b"<< /Length " + str(len(c2)).encode() + b" >>\nstream\n" + c2 + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Title (" + _esc("Harbor Farms trading terms").encode() + b") /Producer (cloud-microfeatures) >>",
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
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 9 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def pdf_pages_text(data: bytes) -> list[str]:
    """The text drawn on each page, in drawing order (only for PDFs written by pdf_bytes)."""
    pages = []
    for m in re.finditer(rb"stream\n(.*?)endstream", data, re.S):
        raw = re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", m.group(1))
        pages.append(" ".join(re.sub(rb"\\(.)", rb"\1", s).decode("latin-1") for s in raw))
    return pages


# ---------- the answer key ----------
def answer_key() -> dict:
    return {term: {"page": page, "section": heading} for page, heading, _, term in SECTIONS}


def summarise() -> dict:
    data = pdf_bytes()
    words = [len(" ".join(t.split()).split()) for t in pdf_pages_text(data)]
    return {"pages": 2, "pdf_bytes": len(data), "sections": len(SECTIONS), "terms": len(SECTIONS),
            "words_per_page": words, "chunk_sizes": list(SIZES), "questions": len(QUESTIONS),
            "answer_key": answer_key(), "queries": 1, "document_ai_pages": 2 * len(SIZES)}


# ---------- BigQuery ----------
def sql(uri: str) -> str:
    if not re.fullmatch(r"gs://[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]/[A-Za-z0-9._/-]+\.pdf", uri):
        raise ValueError("expected a gs://bucket/path.pdf URI")
    parts = [f"SELECT {n} AS requested_size, c.*\nFROM AI.CHUNK_DOCUMENT('{uri}', chunk_size => {n}) AS c"
             for n in SIZES]
    return ("-- Day 43: chunk a supplier terms PDF with AI.CHUNK_DOCUMENT (GA 2026-10-21) at two chunk sizes.\n"
            "-- No connection: the caller's credentials read the object and call the Document AI layout parser.\n"
            + "\nUNION ALL\n".join(parts) + "\n")


def _field(row: dict, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _error_of(row: dict):
    for k, v in row.items():
        if v in (None, "", "[]", "null", "{}"):
            continue
        if "error" in k.lower() or (k.lower() == "status" and str(v).lower() not in OK_STATUS):
            return f"{k}={str(v)[:200]}"
    return None


def check(rows: list[dict]) -> tuple[list[str], dict]:
    problems: list[str] = []
    by_size: dict[int, list[dict]] = {n: [] for n in SIZES}
    for r in rows:
        n = _int(r.get("requested_size"))
        if n in by_size:
            by_size[n].append(r)
    for n, rs in by_size.items():
        if not rs:
            problems.append(f"no chunks for chunk_size {n}")
    errs = [e for e in (_error_of(r) for r in rows) if e]
    if errs:
        problems.append(f"{len(errs)} chunk(s) with errors: {errs[0]}")
    got: dict = {"sizes": {}}
    key = answer_key()
    for n, rs in by_size.items():
        chunks = []
        for r in rs:
            content = _field(r, "content", "chunk", "text", "chunk_text") or ""
            start = _int(_field(r, "start_page", "page_start", "page"))
            end = _int(_field(r, "end_page", "page_end")) or start
            chunks.append((_int(_field(r, "chunk_id", "id")), start, end, content))
        if rs and any(not c[3].strip() for c in chunks):
            problems.append(f"empty chunk text at chunk_size {n}")
        pages = sorted({p for _, s, e, _ in chunks if s is not None for p in range(s, (e or s) + 1)})
        if rs and pages and pages != [1, 2]:
            problems.append(f"chunk_size {n}: chunks span pages {pages}, expected [1, 2]")
        joined = _norm(" ".join(c[3] for c in sorted(chunks, key=lambda c: (c[0] is None, c[0] or 0))))
        missing = [t for t in key if _norm(t) not in joined]
        if rs and missing:
            problems.append(f"chunk_size {n}: terms missing: " + ", ".join(missing))
        whole = {}
        for term, info in key.items():
            hit = next((c for c in chunks if _norm(term) in _norm(c[3])
                        and (c[1] is None or c[1] <= info["page"] <= (c[2] or c[1]))), None)
            whole[term] = None if hit is None else hit[0]
        if rs and not pages:
            got.setdefault("notes", []).append(f"chunk_size {n}: no page columns came back")
        got["sizes"][n] = {"chunks": len(chunks), "longest_chars": max((len(c[3]) for c in chunks), default=0),
                           "terms_whole": sum(v is not None for v in whole.values()),
                           "split_or_wrong_page": [t for t, v in whole.items() if v is None]}
    small, large = (got["sizes"].get(n, {}).get("chunks", 0) for n in SIZES)
    if small and large and small < large:
        problems.append(f"chunk_size {SIZES[0]} gave fewer chunks ({small}) than {SIZES[1]} ({large})")
    got["columns"] = sorted({k for r in rows for k in r})
    return problems, got


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
        print("::error title=Google Cloud day 43 chunk check failed::" + "; ".join(problems)[:900]
              + f" (columns: {', '.join(got.get('columns', []))[:200]})")
        return 1
    n = len(SECTIONS)
    s = got["sizes"]
    parts = [f"chunk_size {k}: {v['chunks']} chunks, longest {v['longest_chars']} chars, {v['terms_whole']}/{n} "
             f"terms whole on the right page" for k, v in s.items()]
    print(f"::notice title=Google Cloud day 43::AI.CHUNK_DOCUMENT split the 2-page supplier terms PDF; all {n} terms "
          f"present at both sizes; " + "; ".join(parts) + f" (columns: {', '.join(got['columns'])[:200]})")
    for k, v in s.items():
        if v["split_or_wrong_page"]:
            print(f"::warning title=Google Cloud day 43::chunk_size {k}: not whole in one chunk on its page: "
                  + ", ".join(v["split_or_wrong_page"]))
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
            {"engine": "answer key written with the PDF (local) / AI.CHUNK_DOCUMENT (cloud)",
             "new_feature": "AI.CHUNK_DOCUMENT", **s}, indent=2) + "\n")
        print(json.dumps({k: v for k, v in s.items() if k != "answer_key"}, indent=2))
