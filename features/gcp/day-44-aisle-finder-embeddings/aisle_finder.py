"""Day 44 (Google Cloud): an aisle finder whose product embeddings maintain themselves, with BigQuery **autonomous
embedding generation** (GA, release note 2026-06-17): a `GENERATED ALWAYS AS (AI.EMBED(...)) STORED
OPTIONS (asynchronous = TRUE)` column that BigQuery fills in background jobs whenever rows arrive, here with the
built-in `embeddinggemma-300m` model (runs on BigQuery slots, no connection, no Vertex AI charge). Shoppers' plain
words are then matched to products with `AI.SEARCH` over that column.

  python aisle_finder.py                         # local: lexical (word-overlap) baseline + answer key, writes result.json
  python aisle_finder.py --sql setup DATASET     # CREATE TABLE with the generated embedding column + the first 11 products
  python aisle_finder.py --sql fresh DATASET     # INSERT the late product (oat milk) after the first fill
  python aisle_finder.py --sql status DATASET    # how many rows have an embedding (polled by cloud.sh)
  python aisle_finder.py --sql search DATASET    # AI.SEARCH for every shopper query, top 3 each
  python aisle_finder.py --sql search_fallback DATASET   # same ranking via ML.DISTANCE if AI.SEARCH is refused
  python aisle_finder.py --status status.json EXPECTED   # exit 0 when EXPECTED rows are embedded, 2 while waiting
  python aisle_finder.py --compare search.json   # cloud.sh: check the search result against the answer key

The cloud run never writes an embedding: it only inserts products. Must hold: every row gets an embedding with an
empty status (all the same length); the oat milk inserted after the first fill gets one too, and is in the top 3 for
"dairy-free milk for my coffee"; at least MIN_CORRECT of the 9 shopper queries put the expected product first.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = "embeddinggemma-300m"
MIN_CORRECT = 6

PRODUCTS = [  # sku, name, aisle, description (what the shelf tag / product page says)
    ("HF-1001", "Whole milk", "Dairy", "Fresh pasteurized cow's milk, 4% fat, one gallon jug"),
    ("HF-2002", "Salted butter", "Dairy", "Churned sweet cream with sea salt, four quarter-pound sticks for baking"),
    ("HF-3001", "Large eggs", "Dairy", "A dozen grade A large brown eggs from cage-free hens"),
    ("PR-4001", "Bananas", "Produce", "Ripe yellow bananas sold by the bunch, a potassium-rich snack"),
    ("PR-4002", "Baby spinach", "Produce", "Washed tender baby spinach leaves in a clamshell"),
    ("BK-5001", "Sourdough loaf", "Bakery", "Crusty naturally leavened sourdough baked in store each morning"),
    ("PN-6001", "Rolled oats", "Pantry", "Old-fashioned whole grain rolled oats for porridge"),
    ("PN-6002", "Spaghetti", "Pantry", "Durum wheat semolina spaghetti, one pound box"),
    ("PN-6003", "Marinara sauce", "Pantry", "Tomato basil marinara in a glass jar"),
    ("HH-7001", "Dish soap", "Household", "Lemon scented liquid soap that cuts grease on pots and pans"),
    ("FZ-8001", "Vanilla ice cream", "Frozen", "Vanilla bean ice cream, one and a half quart tub"),
]
FRESH = ("DA-1005", "Oat milk", "Dairy", "Plant-based oat drink, barista style, foams for lattes")

QUERIES = [  # qid, what a shopper types, the product that should come first
    ("q1", "hot cereal for breakfast", "PN-6001"),
    ("q2", "something to wash the dishes after dinner", "HH-7001"),
    ("q3", "leafy greens for a salad", "PR-4002"),
    ("q4", "cold sweet dessert", "FZ-8001"),
    ("q5", "fruit for a lunchbox", "PR-4001"),
    ("q6", "pasta sauce", "PN-6003"),
    ("q7", "fresh bread", "BK-5001"),
    ("q8", "ingredients for an omelette", "HF-3001"),
    ("q9", "dairy-free milk for my coffee", "DA-1005"),   # only findable once the late row is embedded
]
FRESH_QID = "q9"


# ---------- local baseline: plain word overlap, what a LIKE / keyword search would find
def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9'-]+", text.lower())) - {"a", "an", "for", "the", "my", "to", "of", "in", "on",
                                                            "and", "after", "one", "each", "from", "that"}


def lexical_top(query: str, catalog: list[tuple]) -> tuple[str | None, int]:
    """(best sku, overlap) by shared words with name + description; None when nothing overlaps."""
    best, score = None, 0
    for sku, name, _, desc in catalog:
        s = len(_words(query) & _words(f"{name} {desc}"))
        if s > score:
            best, score = sku, s
    return best, score


def summarise() -> dict:
    catalog = PRODUCTS + [FRESH]
    lex = {qid: lexical_top(q, catalog) for qid, q, _ in QUERIES}
    names = {p[0]: p[1] for p in catalog}
    return {
        "model": MODEL,
        "products_initial": len(PRODUCTS),
        "products_total": len(catalog),
        "queries": len(QUERIES),
        "min_correct": MIN_CORRECT,
        "answer_key": {qid: {"query": q, "sku": sku, "product": names[sku]} for qid, q, sku in QUERIES},
        "lexical_top1": {qid: lex[qid][0] for qid, _, _ in QUERIES},
        "lexical_correct": sum(lex[qid][0] == sku for qid, _, sku in QUERIES),
        "lexical_no_overlap": sum(lex[qid][0] is None for qid, _, _ in QUERIES),
    }


# ---------- BigQuery SQL
def _ds(ds: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", ds):
        raise ValueError(f"bad dataset name {ds!r}")
    return ds


def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _values(rows: list[tuple]) -> str:
    return ",\n  ".join("(" + ", ".join(_q(v) for v in r) + ")" for r in rows)


SETUP_SQL = """-- Day 44: a product table whose description embedding BigQuery generates and maintains in the background
CREATE TABLE `{ds}.products` (
  sku STRING, name STRING, aisle STRING, description STRING,
  description_embedding STRUCT<result ARRAY<FLOAT64>, status STRING>
    GENERATED ALWAYS AS (AI.EMBED(description, model => '{model}'))
    STORED OPTIONS (asynchronous = TRUE)
);
INSERT INTO `{ds}.products` (sku, name, aisle, description) VALUES
  {rows};
"""

FRESH_SQL = """-- Day 44: a product arrives after the first fill; no embedding is written by hand
INSERT INTO `{ds}.products` (sku, name, aisle, description) VALUES
  {rows};
"""

STATUS_SQL = """-- Day 44: progress of the background embedding jobs
SELECT
  COUNT(*) AS total,
  COUNTIF(description_embedding.result IS NOT NULL AND IFNULL(description_embedding.status, '') = '') AS embedded,
  COUNTIF(IFNULL(description_embedding.status, '') != '') AS errored,
  MIN(ARRAY_LENGTH(description_embedding.result)) AS dims_min,
  MAX(ARRAY_LENGTH(description_embedding.result)) AS dims_max,
  STRING_AGG(DISTINCT NULLIF(description_embedding.status, ''), ' | ') AS errors,
  LOGICAL_OR(sku = '{fresh}' AND description_embedding.result IS NOT NULL) AS fresh_embedded
FROM `{ds}.products`
"""

SEARCH_SQL = """-- Day 44: shopper words -> products with AI.SEARCH over the generated embedding column
SELECT qid, sku, distance, rnk FROM (
  SELECT qid, sku, distance, ROW_NUMBER() OVER (PARTITION BY qid ORDER BY distance, sku) AS rnk FROM (
{arms}
  )
) WHERE rnk <= 3
ORDER BY qid, rnk
"""
SEARCH_ARM = "    SELECT '{qid}' AS qid, base.sku AS sku, distance FROM AI.SEARCH(TABLE `{ds}.products`, 'description', {text})"

FALLBACK_SQL = """-- Day 44 fallback: same ranking from the generated column, query embedded with the same built-in model
WITH q AS (
  SELECT qid, text FROM UNNEST([
    {structs}
  ])
), qe AS (
  SELECT qid, AI.EMBED(text, model => '{model}').result AS v FROM q
)
SELECT qid, sku, distance, rnk FROM (
  SELECT qe.qid, p.sku, ML.DISTANCE(p.description_embedding.result, qe.v, 'COSINE') AS distance,
         ROW_NUMBER() OVER (PARTITION BY qe.qid
                            ORDER BY ML.DISTANCE(p.description_embedding.result, qe.v, 'COSINE'), p.sku) AS rnk
  FROM qe CROSS JOIN `{ds}.products` AS p
  WHERE p.description_embedding.result IS NOT NULL
) WHERE rnk <= 3
ORDER BY qid, rnk
"""


def bigquery_sql(part: str, ds: str) -> str:
    ds = _ds(ds)
    if part == "setup":
        return SETUP_SQL.format(ds=ds, model=MODEL, rows=_values(PRODUCTS))
    if part == "fresh":
        return FRESH_SQL.format(ds=ds, rows=_values([FRESH]))
    if part == "status":
        return STATUS_SQL.format(ds=ds, fresh=FRESH[0])
    if part == "search":
        arms = "\n    UNION ALL\n".join(SEARCH_ARM.format(qid=qid, ds=ds, text=_q(q)) for qid, q, _ in QUERIES)
        return SEARCH_SQL.format(arms=arms)
    if part == "search_fallback":
        structs = ",\n    ".join(f"STRUCT({_q(qid)} AS qid, {_q(q)} AS text)" for qid, q, _ in QUERIES)
        return FALLBACK_SQL.format(ds=ds, model=MODEL, structs=structs)
    raise ValueError(part)


# ---------- cloud output
def _redact(text: str) -> str:
    return re.sub(r"\d{6,}", "<number>", text.replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>"))


def read_rows(path: Path) -> list[dict]:
    """bq --format=json output (may start with a warning line) -> list of row dicts."""
    text = path.read_text()
    dec = json.JSONDecoder()
    for i in (k for k, ch in enumerate(text) if ch == "["):
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, list) and all(isinstance(r, dict) for r in obj):
            return obj
    print(f"::error title=Unreadable BigQuery output::{path.name}: {_redact(text[:400])!r}")
    raise SystemExit(1)


def check_status(row: dict, expected: int) -> tuple[str, list[str]]:
    """('done' | 'waiting' | 'failed', messages)."""
    n = lambda k: int(float(row.get(k) or 0))
    total, embedded, errored = n("total"), n("embedded"), n("errored")
    if errored:
        return "failed", [f"{errored} row(s) have an embedding status: {_redact(str(row.get('errors')))[:300]}"]
    if total != expected:
        return "failed", [f"table has {total} rows, expected {expected}"]
    if embedded < expected:
        return "waiting", [f"{embedded}/{expected} embedded"]
    if n("dims_min") != n("dims_max") or n("dims_min") == 0:
        return "failed", [f"embedding lengths differ: {row.get('dims_min')}..{row.get('dims_max')}"]
    return "done", [f"{embedded}/{expected} embedded, {n('dims_min')} dimensions"]


def evaluate(rows: list[dict]) -> dict:
    by_q: dict[str, list[tuple[int, str, float]]] = {}
    for r in rows:
        by_q.setdefault(r["qid"], []).append((int(float(r["rnk"])), r["sku"], float(r["distance"])))
    top = {q: [s for _, s, _ in sorted(v)] for q, v in by_q.items()}
    key = {qid: sku for qid, _, sku in QUERIES}
    errors = []
    missing = sorted(set(key) - set(top))
    if missing:
        errors.append(f"no hits for {missing}")
    correct = [q for q in key if top.get(q, [None])[0] == key[q]]
    if len(correct) < MIN_CORRECT:
        errors.append(f"only {len(correct)}/{len(key)} queries put the expected product first (need {MIN_CORRECT})")
    if FRESH[0] not in top.get(FRESH_QID, []):
        errors.append(f"the late-arriving {FRESH[1]} is not in the top 3 for {FRESH_QID}: {top.get(FRESH_QID)}")
    return {"top3": top, "correct": len(correct), "wrong": {q: top.get(q, [None])[0] for q in key if q not in correct},
            "fresh_rank": (top.get(FRESH_QID, []).index(FRESH[0]) + 1) if FRESH[0] in top.get(FRESH_QID, []) else None,
            "errors": errors}


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "--local"
    if mode == "--sql":
        print(bigquery_sql(argv[1], argv[2] if len(argv) > 2 else "DATASET"))
        return 0
    if mode == "--status":
        rows = read_rows(Path(argv[1]))
        state, msgs = check_status(rows[0] if rows else {}, int(argv[2]))
        print("; ".join(msgs))
        if state == "failed":
            print(f"::error title=Day 44 embedding generation::{_redact('; '.join(msgs))}")
            return 1
        return 0 if state == "done" else 2
    if mode == "--compare":
        e = evaluate(read_rows(Path(argv[1])))
        s = summarise()
        if e["errors"]:
            print(f"::error title=Day 44 aisle finder::{_redact('; '.join(e['errors']))[:850]}")
            return 1
        wrong = ", ".join(f"{q} got {sku}" for q, sku in e["wrong"].items()) or "none"
        print(f"::notice title=Google Cloud day 44::AI.SEARCH over an autonomously generated {MODEL} column put the "
              f"expected product first for {e['correct']}/{s['queries']} shopper queries (word overlap: "
              f"{s['lexical_correct']}/{s['queries']}); the oat milk inserted after the first fill was embedded by "
              f"BigQuery and ranked #{e['fresh_rank']} for '{s['answer_key'][FRESH_QID]['query']}'. Misses: {wrong}")
        print("DAY44_RESULT " + json.dumps({k: e[k] for k in ("correct", "fresh_rank", "wrong")}))
        return 0
    s = summarise()
    (HERE / "result.json").write_text(json.dumps(
        {"engine": "word-overlap baseline + answer key (local) / autonomous embedding generation + AI.SEARCH (cloud)",
         **s}, indent=2) + "\n")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
