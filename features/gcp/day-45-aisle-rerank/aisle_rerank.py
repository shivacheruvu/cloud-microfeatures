"""Day 45 (Google Cloud): the day-44 aisle finder's shopper queries answered by **AI.RANK** (GA, release note
2026-11-17): "reorder a table's rows by how well the text in a specified column answers a search query". Unlike an
embedding search, which embeds the query and each description separately and compares vectors, AI.RANK reads the
query and each row's text together and returns the rows with a relevance score and a rank position.

  python aisle_rerank.py                          # local: word-overlap baseline + day-44 embedding result, result.json
  python aisle_rerank.py --sql setup DATASET      # CREATE TABLE products + the same 12 products as day 44 (no embeddings)
  python aisle_rerank.py --sql probe:N DATASET    # call form N for one query (cloud.sh tries them in order)
  python aisle_rerank.py --sql rank:N DATASET     # call form N for all 9 shopper queries
  python aisle_rerank.py --compare rank.json      # cloud.sh: check the ranking against the answer key

Call forms (tried in order; the first one BigQuery accepts is used and named in the result annotation):
  1. AI.RANK(TABLE products, 'description', query)
  2. AI.RANK((SELECT sku, name, description FROM products), 'description', query)
  3. AI.RANK(TABLE products, 'description', query, top_k => 12)
Each row comes back as TO_JSON_STRING of the whole output row, so the check finds the sku and the rank / score
columns by name whatever shape the output has.

Must hold: every query returns at least 3 ranked rows; at least MIN_CORRECT of the 9 queries put the expected product
first (day 44's embedding search got 7/9; word overlap gets 1/9).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_CORRECT = 7
DAY44 = {"engine": "AI.SEARCH over an embeddinggemma-300m generated column (day 44 cloud run, 2026-11-17)",
         "correct": 7, "misses": {"q6": "PN-6002", "q9": "HF-1001"}}

PRODUCTS = [  # same catalog as day 44 (sku, name, aisle, description), oat milk included from the start
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
    ("DA-1005", "Oat milk", "Dairy", "Plant-based oat drink, barista style, foams for lattes"),
]

QUERIES = [  # qid, what a shopper types, the product that should come first (day 44's answer key)
    ("q1", "hot cereal for breakfast", "PN-6001"),
    ("q2", "something to wash the dishes after dinner", "HH-7001"),
    ("q3", "leafy greens for a salad", "PR-4002"),
    ("q4", "cold sweet dessert", "FZ-8001"),
    ("q5", "fruit for a lunchbox", "PR-4001"),
    ("q6", "pasta sauce", "PN-6003"),
    ("q7", "fresh bread", "BK-5001"),
    ("q8", "ingredients for an omelette", "HF-3001"),
    ("q9", "dairy-free milk for my coffee", "DA-1005"),
]
PROBE_QID = "q6"   # day 44's miss: the marinara tag says neither "pasta" nor "sauce"


# ---------- local baseline: plain word overlap, what a LIKE / keyword search would find
def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9'-]+", text.lower())) - {"a", "an", "for", "the", "my", "to", "of", "in", "on",
                                                            "and", "after", "one", "each", "from", "that"}


def lexical_top(query: str) -> str | None:
    best, score = None, 0
    for sku, name, _, desc in PRODUCTS:
        s = len(_words(query) & _words(f"{name} {desc}"))
        if s > score:
            best, score = sku, s
    return best


def summarise() -> dict:
    names = {p[0]: p[1] for p in PRODUCTS}
    lex = {qid: lexical_top(q) for qid, q, _ in QUERIES}
    return {
        "products": len(PRODUCTS),
        "queries": len(QUERIES),
        "min_correct": MIN_CORRECT,
        "answer_key": {qid: {"query": q, "sku": sku, "product": names[sku]} for qid, q, sku in QUERIES},
        "lexical_top1": lex,
        "lexical_correct": sum(lex[qid] == sku for qid, _, sku in QUERIES),
        "day44_embedding_search": DAY44,
    }


# ---------- BigQuery SQL
FORMS = {
    1: "AI.RANK(TABLE `{ds}.products`, 'description', {text})",
    2: "AI.RANK((SELECT sku, name, description FROM `{ds}.products`), 'description', {text})",
    3: "AI.RANK(TABLE `{ds}.products`, 'description', {text}, top_k => 12)",
}
FORM_LABELS = {"1": "TABLE products, 'description', query", "2": "(SELECT ...), 'description', query",
               "3": "TABLE products, 'description', query, top_k => 12"}


def _ds(ds: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", ds):
        raise ValueError(f"bad dataset name {ds!r}")
    return ds


def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


SETUP_SQL = """-- Day 45: the day-44 catalog as a plain table; AI.RANK needs no embedding column
CREATE TABLE `{ds}.products` (sku STRING, name STRING, aisle STRING, description STRING);
INSERT INTO `{ds}.products` (sku, name, aisle, description) VALUES
  {rows};
"""

ARM = "SELECT {qid} AS qid, TO_JSON_STRING(r) AS j FROM {call} AS r"


def bigquery_sql(part: str, ds: str) -> str:
    ds = _ds(ds)
    if part == "setup":
        rows = ",\n  ".join("(" + ", ".join(_q(v) for v in p) + ")" for p in PRODUCTS)
        return SETUP_SQL.format(ds=ds, rows=rows)
    kind, _, n = part.partition(":")
    form = FORMS[int(n)]
    if kind == "probe":
        q = dict((qid, text) for qid, text, _ in QUERIES)[PROBE_QID]
        return f"-- Day 45: does BigQuery accept AI.RANK call form {n}?\n" + \
            ARM.format(qid=_q(PROBE_QID), call=form.format(ds=ds, text=_q(q)))
    if kind == "rank":
        arms = "\nUNION ALL\n".join(ARM.format(qid=_q(qid), call=form.format(ds=ds, text=_q(q)))
                                    for qid, q, _ in QUERIES)
        return f"-- Day 45: AI.RANK (call form {n}) over the catalog for every shopper query\n{arms}\n"
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


def _flat(obj, prefix="") -> dict:
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flat(v, f"{prefix}{k}."))
    else:
        out[prefix.rstrip(".")] = obj
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_row(j: str) -> dict:
    """One AI.RANK output row (as JSON) -> {sku, rank, score, keys}. Rank / score columns are found by name."""
    flat = _flat(json.loads(j))
    sku = next((v for k, v in flat.items() if k.split(".")[-1] == "sku"), None)
    leaf = {k: k.split(".")[-1].lower() for k in flat}
    rank_k = next((k for k, n in leaf.items() if "rank" in n and "score" not in n and _num(flat[k]) is not None), None)
    score_k = next((k for k, n in leaf.items() if ("score" in n or "relevance" in n) and _num(flat[k]) is not None),
                   None)
    return {"sku": sku, "rank": _num(flat[rank_k]) if rank_k else None,
            "score": _num(flat[score_k]) if score_k else None,
            "rank_col": rank_k, "score_col": score_k, "keys": sorted(flat)}


def evaluate(rows: list[dict]) -> dict:
    by_q: dict[str, list[dict]] = {}
    for r in rows:
        by_q.setdefault(r["qid"], []).append(parse_row(r["j"]))
    errors, top = [], {}
    cols = {"rank": None, "score": None}
    for qid, items in by_q.items():
        if any(i["sku"] is None for i in items):
            errors.append(f"{qid}: no sku in the output row (keys {items[0]['keys']})")
            continue
        if all(i["rank"] is not None for i in items):
            order = sorted(items, key=lambda i: (i["rank"], i["sku"]))
        elif all(i["score"] is not None for i in items):
            order = sorted(items, key=lambda i: (-i["score"], i["sku"]))
        else:
            errors.append(f"{qid}: neither a rank nor a score column (keys {items[0]['keys']})")
            continue
        cols = {"rank": items[0]["rank_col"], "score": items[0]["score_col"]}
        top[qid] = [i["sku"] for i in order]
    key = {qid: sku for qid, _, sku in QUERIES}
    missing = sorted(set(key) - set(by_q))
    if missing:
        errors.append(f"no rows for {missing}")
    short = sorted(q for q, v in top.items() if len(v) < 3)
    if short:
        errors.append(f"fewer than 3 ranked rows for {short}")
    correct = [q for q in key if top.get(q, [None])[0] == key[q]]
    if len(correct) < MIN_CORRECT and not errors:
        errors.append(f"only {len(correct)}/{len(key)} queries put the expected product first (need {MIN_CORRECT})")
    return {"top3": {q: v[:3] for q, v in top.items()}, "correct": len(correct),
            "rows_per_query": min((len(v) for v in top.values()), default=0),
            "wrong": {q: top.get(q, [None])[0] for q in key if q not in correct},
            "fixed_day44_misses": sorted(q for q in DAY44["misses"] if q in correct),
            "columns": cols, "errors": errors}


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "--local"
    if mode == "--sql":
        print(bigquery_sql(argv[1], argv[2] if len(argv) > 2 else "DATASET"))
        return 0
    if mode == "--compare":
        form = argv[2] if len(argv) > 2 else "?"
        e = evaluate(read_rows(Path(argv[1])))
        s = summarise()
        if e["errors"]:
            print(f"::error title=Day 45 AI.RANK::{_redact('; '.join(e['errors']))[:850]}")
            return 1
        wrong = ", ".join(f"{q} got {sku}" for q, sku in e["wrong"].items()) or "none"
        fixed = ", ".join(f"{q} ('{s['answer_key'][q]['query']}')" for q in e["fixed_day44_misses"]) or "neither"
        print(f"::notice title=Google Cloud day 45::AI.RANK (call form {form}: {FORM_LABELS.get(form, '?')}) put "
              f"the expected product first for "
              f"{e['correct']}/{s['queries']} shopper queries ({e['rows_per_query']}+ rows each, ordered by "
              f"{e['columns']['rank'] or e['columns']['score']}); day 44's embedding search got "
              f"{DAY44['correct']}/9 and word overlap {s['lexical_correct']}/9. Day-44 misses fixed: {fixed}. "
              f"Misses: {wrong}")
        print("DAY45_RESULT " + json.dumps({k: e[k] for k in ("correct", "wrong", "fixed_day44_misses", "columns")}))
        return 0
    s = summarise()
    (HERE / "result.json").write_text(json.dumps(
        {"engine": "word-overlap baseline + day-44 embedding result + answer key (local) / AI.RANK (cloud)", **s},
        indent=2) + "\n")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
