"""Day 46 (Google Cloud): the aisle finder as a two-stage search in one function call: **AI.SEARCH with the `rerank`
option** (GA, BigQuery release note 2026-11-17). Stage 1 is day 44's vector search over an autonomously generated
`embeddinggemma-300m` column (top 5 candidates per query); stage 2 has the ranking model read the query and each
candidate's text together (what day 45 did with AI.RANK over the whole table) and reorders the candidates.

  python aisle_search_rerank.py                          # local: answer key + day 44 / 45 baselines, result.json
  python aisle_search_rerank.py --sql setup DATASET      # products table with the generated embedding column, 12 rows
  python aisle_search_rerank.py --sql status DATASET     # how many rows BigQuery has embedded (polled by cloud.sh)
  python aisle_search_rerank.py --sql probe:N DATASET    # call form N for one query (cloud.sh tries them in order)
  python aisle_search_rerank.py --sql search:N DATASET   # call form N for all 9 shopper queries
  python aisle_search_rerank.py --status status.json 12  # exit 0 when all rows are embedded, 2 while waiting
  python aisle_search_rerank.py --compare search.json N  # cloud.sh: check against the answer key

Call forms (tried in order; the first one BigQuery accepts is used and named in the result annotation). The AI.SEARCH
reference page could not be read from the build session, so the argument shape is probed, as day 45 did:
  1. AI.SEARCH(TABLE products, 'description', query, top_k => 5, rerank => TRUE)
  2. AI.SEARCH(TABLE products, 'description', query, top_k => 5, rerank => 'semantic-ranker-default@latest')
  3. AI.SEARCH(TABLE products, 'description', query, top_k => 5, options => JSON '{"rerank": true}')
  4. AI.SEARCH(TABLE products, 'description', query, rerank => TRUE)
Each row comes back as TO_JSON_STRING of the whole output row; the check finds the sku and the rerank score / rank
column by name. A row set with no score or rank column fails (the reranker's output is the point of the day).

Must hold: every query returns at least 3 candidates; at least MIN_CORRECT of the 9 queries put the expected product
first (day 44's vector search alone got 6-7/9; day 45's AI.RANK over the whole table got 9/9).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = "embeddinggemma-300m"
TOP_K = 5
MIN_CORRECT = 7
BASELINES = {
    "day44_vector_search": {"engine": "AI.SEARCH over the generated column, no rerank (day 44)", "correct": "6-7/9"},
    "day45_ai_rank": {"engine": "AI.RANK over all 12 rows (day 45)", "correct": "9/9"},
}

PRODUCTS = [  # same catalog as days 44 and 45 (sku, name, aisle, description)
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
PROBE_QID = "q6"


def summarise() -> dict:
    names = {p[0]: p[1] for p in PRODUCTS}
    return {
        "model": MODEL,
        "products": len(PRODUCTS),
        "queries": len(QUERIES),
        "top_k": TOP_K,
        "min_correct": MIN_CORRECT,
        "answer_key": {qid: {"query": q, "sku": sku, "product": names[sku]} for qid, q, sku in QUERIES},
        "baselines": BASELINES,
    }


# ---------- BigQuery SQL
FORMS = {
    1: "AI.SEARCH(TABLE `{ds}.products`, 'description', {text}, top_k => {k}, rerank => TRUE)",
    2: "AI.SEARCH(TABLE `{ds}.products`, 'description', {text}, top_k => {k}, "
       "rerank => 'semantic-ranker-default@latest')",
    3: "AI.SEARCH(TABLE `{ds}.products`, 'description', {text}, top_k => {k}, options => JSON '{{\"rerank\": true}}')",
    4: "AI.SEARCH(TABLE `{ds}.products`, 'description', {text}, rerank => TRUE)",
}
FORM_LABELS = {"1": "top_k => 5, rerank => TRUE", "2": "top_k => 5, rerank => 'semantic-ranker-default@latest'",
               "3": "top_k => 5, options => JSON rerank", "4": "rerank => TRUE"}


def _ds(ds: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", ds):
        raise ValueError(f"bad dataset name {ds!r}")
    return ds


def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


SETUP_SQL = """-- Day 46: the catalog with a description embedding BigQuery generates in the background (as day 44)
CREATE TABLE `{ds}.products` (
  sku STRING, name STRING, aisle STRING, description STRING,
  description_embedding STRUCT<result ARRAY<FLOAT64>, status STRING>
    GENERATED ALWAYS AS (AI.EMBED(description, model => '{model}'))
    STORED OPTIONS (asynchronous = TRUE)
);
INSERT INTO `{ds}.products` (sku, name, aisle, description) VALUES
  {rows};
"""

STATUS_SQL = """-- Day 46: progress of the background embedding jobs
SELECT
  COUNT(*) AS total,
  COUNTIF(description_embedding.result IS NOT NULL AND IFNULL(description_embedding.status, '') = '') AS embedded,
  COUNTIF(IFNULL(description_embedding.status, '') != '') AS errored,
  STRING_AGG(DISTINCT NULLIF(description_embedding.status, ''), ' | ') AS errors
FROM `{ds}.products`
"""

ARM = "SELECT {qid} AS qid, TO_JSON_STRING(r) AS j FROM {call} AS r"


def bigquery_sql(part: str, ds: str) -> str:
    ds = _ds(ds)
    if part == "setup":
        rows = ",\n  ".join("(" + ", ".join(_q(v) for v in p) + ")" for p in PRODUCTS)
        return SETUP_SQL.format(ds=ds, model=MODEL, rows=rows)
    if part == "status":
        return STATUS_SQL.format(ds=ds)
    kind, _, n = part.partition(":")
    form = FORMS[int(n)]
    if kind == "probe":
        q = dict((qid, text) for qid, text, _ in QUERIES)[PROBE_QID]
        return f"-- Day 46: does BigQuery accept AI.SEARCH rerank call form {n}?\n" + \
            ARM.format(qid=_q(PROBE_QID), call=form.format(ds=ds, text=_q(q), k=TOP_K))
    if kind == "search":
        arms = "\nUNION ALL\n".join(ARM.format(qid=_q(qid), call=form.format(ds=ds, text=_q(q), k=TOP_K))
                                    for qid, q, _ in QUERIES)
        return f"-- Day 46: AI.SEARCH with rerank (call form {n}) for every shopper query\n{arms}\n"
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


def check_status(row: dict, expected: int) -> tuple[str, str]:
    n = lambda k: int(float(row.get(k) or 0))
    if n("errored"):
        return "failed", f"{n('errored')} row(s) have an embedding status: {_redact(str(row.get('errors')))[:300]}"
    if n("total") != expected:
        return "failed", f"table has {n('total')} rows, expected {expected}"
    if n("embedded") < expected:
        return "waiting", f"{n('embedded')}/{expected} embedded"
    return "done", f"{n('embedded')}/{expected} embedded"


def _flat(obj, prefix="") -> dict:
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flat(v, f"{prefix}{k}."))
    elif isinstance(obj, list):
        pass  # embedding vectors and other arrays are not needed
    else:
        out[prefix.rstrip(".")] = obj
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_row(j: str) -> dict:
    """One AI.SEARCH output row (as JSON) -> {sku, rank, score, distance, keys}. Columns are found by name: the
    rerank output is a column whose name has 'rank' (position) or 'score' / 'relevance' (higher is better)."""
    flat = _flat(json.loads(j))
    leaf = {k: k.split(".")[-1].lower() for k in flat}
    sku = next((v for k, v in flat.items() if leaf[k] == "sku"), None)
    numeric = {k: n for k, n in leaf.items() if _num(flat[k]) is not None and not isinstance(flat[k], bool)}
    rank_k = next((k for k, n in numeric.items() if "rank" in n and "score" not in n), None)
    score_k = next((k for k, n in numeric.items() if "score" in n or "relevance" in n), None)
    dist_k = next((k for k, n in numeric.items() if n == "distance"), None)
    return {"sku": sku, "rank": _num(flat[rank_k]) if rank_k else None,
            "score": _num(flat[score_k]) if score_k else None,
            "distance": _num(flat[dist_k]) if dist_k else None,
            "rank_col": rank_k, "score_col": score_k, "keys": sorted(flat)}


def evaluate(rows: list[dict]) -> dict:
    by_q: dict[str, list[dict]] = {}
    for r in rows:
        by_q.setdefault(r["qid"], []).append(parse_row(r["j"]))
    errors, top, by_distance = [], {}, {}
    cols = {"rank": None, "score": None}
    for qid, items in sorted(by_q.items()):
        if any(i["sku"] is None for i in items):
            errors.append(f"{qid}: no sku in the output row (keys {items[0]['keys']})")
            continue
        if all(i["rank"] is not None for i in items):
            order = sorted(items, key=lambda i: (i["rank"], i["sku"]))
        elif all(i["score"] is not None for i in items):
            order = sorted(items, key=lambda i: (-i["score"], i["sku"]))
        else:
            errors.append(f"{qid}: no rerank score or rank column (keys {items[0]['keys']})")
            continue
        cols = {"rank": items[0]["rank_col"], "score": items[0]["score_col"]}
        top[qid] = [i["sku"] for i in order]
        if all(i["distance"] is not None for i in items):
            by_distance[qid] = min(items, key=lambda i: (i["distance"], i["sku"]))["sku"]
    key = {qid: sku for qid, _, sku in QUERIES}
    missing = sorted(set(key) - set(by_q))
    if missing:
        errors.append(f"no rows for {missing}")
    short = sorted(q for q, v in top.items() if len(v) < 3)
    if short:
        errors.append(f"fewer than 3 candidates for {short}")
    correct = [q for q in key if top.get(q, [None])[0] == key[q]]
    if len(correct) < MIN_CORRECT and not errors:
        errors.append(f"only {len(correct)}/{len(key)} queries put the expected product first (need {MIN_CORRECT})")
    in_candidates = sum(key[q] in top.get(q, []) for q in key)
    reordered = sorted(q for q in by_distance if top.get(q) and top[q][0] != by_distance[q])
    return {"top3": {q: v[:3] for q, v in top.items()}, "correct": len(correct),
            "candidates_per_query": min((len(v) for v in top.values()), default=0),
            "answer_in_candidates": in_candidates,
            "vector_top1_correct": sum(by_distance.get(q) == key[q] for q in key) if by_distance else None,
            "reordered_by_rerank": reordered,
            "wrong": {q: top.get(q, [None])[0] for q in key if q not in correct},
            "columns": cols, "errors": errors}


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "--local"
    if mode == "--sql":
        print(bigquery_sql(argv[1], argv[2] if len(argv) > 2 else "DATASET"))
        return 0
    if mode == "--status":
        rows = read_rows(Path(argv[1]))
        state, msg = check_status(rows[0] if rows else {}, int(argv[2]))
        print(msg)
        if state == "failed":
            print(f"::error title=Day 46 embedding generation::{_redact(msg)}")
            return 1
        return 0 if state == "done" else 2
    if mode == "--compare":
        form = argv[2] if len(argv) > 2 else "?"
        e = evaluate(read_rows(Path(argv[1])))
        if e["errors"]:
            print(f"::error title=Day 46 AI.SEARCH rerank::{_redact('; '.join(e['errors']))[:850]}")
            return 1
        wrong = ", ".join(f"{q} got {sku}" for q, sku in e["wrong"].items()) or "none"
        vec = e["vector_top1_correct"]
        print(f"::notice title=Google Cloud day 46::AI.SEARCH with rerank (call form {form}: "
              f"{FORM_LABELS.get(form, '?')}) put the expected product first for {e['correct']}/{len(QUERIES)} "
              f"shopper queries ({e['candidates_per_query']}+ candidates each, ordered by "
              f"{e['columns']['rank'] or e['columns']['score']}); answer among the candidates "
              f"{e['answer_in_candidates']}/9; vector distance alone "
              f"{'n/a' if vec is None else f'{vec}/9'}; rerank changed the top pick for "
              f"{len(e['reordered_by_rerank'])} queries. Misses: {wrong}")
        print("DAY46_RESULT " + json.dumps({k: e[k] for k in ("correct", "answer_in_candidates",
                                                               "vector_top1_correct", "reordered_by_rerank",
                                                               "wrong", "columns")}))
        return 0
    s = summarise()
    prev = json.loads((HERE / "result.json").read_text()) if (HERE / "result.json").exists() else {}
    cloud = {"cloud": prev["cloud"]} if "cloud" in prev else {}
    (HERE / "result.json").write_text(json.dumps(
        {"engine": "answer key + day 44 / 45 baselines (local) / AI.SEARCH with rerank (cloud)", **s, **cloud},
        indent=2) + "\n")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
