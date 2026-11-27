"""Day 53 (Google Cloud): match shoppers' search words ("aubergine", "kitchen roll", "nappies") to the US catalog
names ("Eggplant", "Paper Towels", "Diapers") with BigQuery's AI.SIMILARITY on the embeddinggemma-300m model
(GA in AI.EMBED and AI.SIMILARITY, BigQuery release notes 2026-11-23), compared with gemini-embedding-2 (day 26).

  python matcher.py                         # local: character-trigram cosine baseline; writes result.json
  python matcher.py --sql [ENDPOINT]        # print the BigQuery SQL (searches and products inlined, no table)
  python matcher.py --compare OUT [GEMINI]  # check bq's JSON output for embeddinggemma-300m (and, optionally, the
                                            # gemini-embedding-2 run) and print annotations (cloud.sh)

16 search terms x 16 products = 256 pairs. A search's match is the product with the highest score; the answer key is
in data/searches.csv. Half the terms share a word with their product ("porridge oats" / "Rolled Oats"), half share
nothing but meaning ("courgette" / "Zucchini"), which is where a fuzzy matcher has nothing to go on.

The cloud check fails only on what must hold whatever the exact scores: all 256 pairs come back with a score in
[-1, 1]. Accuracy is reported next to the baseline and gemini-embedding-2; doing worse than the trigram baseline is a
warning (a finding about the model, not a broken run).
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENDPOINT = "embeddinggemma-300m"
COMPARE_ENDPOINT = "gemini-embedding-2"


def _csv(name: str) -> list[dict]:
    with open(HERE / "data" / name, newline="") as f:
        return list(csv.DictReader(f))


def catalog() -> list[dict]:
    return _csv("catalog.csv")


def searches() -> list[dict]:
    return _csv("searches.csv")


# ---------------------------------------------------------------- local baseline
def trigrams(text: str) -> Counter:
    t = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "
    return Counter(t[i:i + 3] for i in range(len(t) - 2))


def trigram_cosine(a: str, b: str) -> float:
    x, y = trigrams(a), trigrams(b)
    dot = sum(x[k] * y[k] for k in x)
    norm = math.sqrt(sum(v * v for v in x.values())) * math.sqrt(sum(v * v for v in y.values()))
    return dot / norm if norm else 0.0


def local_scores() -> dict[tuple[str, str], float]:
    return {(s["search"], p["sku"]): trigram_cosine(s["search"], p["name"]) for s in searches() for p in catalog()}


# ---------------------------------------------------------------- matching and summary
def matches(scores: dict[tuple[str, str], float]) -> dict[str, dict]:
    """Per search: the best product, its score and the margin over the runner-up (ties go to the lower SKU).
    A best score of 0 or less means nothing matched at all (no shared trigram), so it counts as no match rather than
    a lucky tie on the first SKU."""
    out = {}
    for s in searches():
        ranked = sorted(((scores[(s["search"], p["sku"])], p["sku"]) for p in catalog()), key=lambda r: (-r[0], r[1]))
        (best, sku), (second, _) = ranked[0], ranked[1]
        if best <= 0:
            sku = ""
        out[s["search"]] = {"sku": sku, "score": round(best, 4), "margin": round(best - second, 4),
                            "correct": sku == s["expected_sku"]}
    return out


def summarise(scores: dict[tuple[str, str], float]) -> dict:
    m = matches(scores)
    names = {p["sku"]: p["name"] for p in catalog()} | {"": "(no match)"}
    right = [t for t, r in m.items() if r["correct"]]
    return {
        "pairs_scored": len(scores),
        "searches": len(m),
        "top1_correct": len(right),
        "top1_accuracy_pct": round(100 * len(right) / len(m), 1),
        "mean_margin": round(sum(r["margin"] for r in m.values()) / len(m), 4),
        "wrong": {t: names[r["sku"]] for t, r in m.items() if not r["correct"]},
    }


# ---------------------------------------------------------------- BigQuery
def sql(endpoint: str = ENDPOINT) -> str:
    ts = ", ".join(json.dumps(s["search"]) for s in searches())
    ps = ",\n    ".join(f"STRUCT({json.dumps(p['sku'])} AS sku, {json.dumps(p['name'])} AS name)" for p in catalog())
    return f"""-- Day 53: score every (search term, catalog product) pair with AI.SIMILARITY on {endpoint}.
-- TO_JSON_STRING keeps a NULL (an error) visible as "null".
SELECT search, sku,
       TO_JSON_STRING(AI.SIMILARITY(content1 => search, content2 => name, endpoint => '{endpoint}')) AS raw
FROM UNNEST([{ts}]) AS search
CROSS JOIN UNNEST([
    {ps}
])
ORDER BY search, sku
"""


def score_from_json(raw) -> float | None:
    v = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(v, dict):
        v = v.get("result", v.get("similarity"))
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def read_scores(path: str) -> tuple[dict | None, str]:
    """bq JSON output -> {(search, sku): score}, or (None, problem)."""
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a --scopes warning before the JSON
    try:
        rows = json.loads(text[start:]) if start >= 0 else []
    except ValueError as e:
        return None, f"unreadable BigQuery output: {e}"
    cloud = {(r["search"], r["sku"]): score_from_json(r.get("raw")) for r in rows}
    local = local_scores()
    if set(cloud) != set(local):
        return None, f"{len(cloud)} pairs back, expected {len(local)}"
    bad = [k for k, s in cloud.items() if s is None or not -1.0001 <= s <= 1.0001]
    if bad:
        sample = next((r.get("raw") for r in rows if (r["search"], r["sku"]) == bad[0]), None)
        return None, f"{len(bad)} pairs without a score in [-1, 1] (first raw value: {str(sample)[:200]})"
    return cloud, ""


def compare(out_path: str, gemini_path: str | None = None) -> int:
    gemma, problem = read_scores(out_path)
    if gemma is None:
        print(f"::error title=Google Cloud day 53 ({ENDPOINT}) differs from local::{problem}")
        return 1
    g, base = summarise(gemma), summarise(local_scores())
    msg = (f"AI.SIMILARITY ({ENDPOINT}) scored all {g['pairs_scored']} search-term x product pairs: top-1 right for "
           f"{g['top1_correct']} of {g['searches']} searches ({g['top1_accuracy_pct']}%, mean margin "
           f"{g['mean_margin']}) vs {base['top1_correct']} of {base['searches']} for the local trigram baseline")
    if gemini_path:
        gem, gproblem = read_scores(gemini_path)
        if gem is None:
            print(f"::warning title=Google Cloud day 53 comparison ({COMPARE_ENDPOINT})::{gproblem}")
        else:
            c = summarise(gem)
            msg += f" and {c['top1_correct']} of {c['searches']} for {COMPARE_ENDPOINT} (mean margin {c['mean_margin']})"
            print(f"::notice title=Google Cloud day 53 {COMPARE_ENDPOINT} misses::{json.dumps(c['wrong'])[:300]}")
    print(f"::notice title=Google Cloud day 53::{msg}. {ENDPOINT} misses: {json.dumps(g['wrong'])[:300]}")
    if g["top1_correct"] < base["top1_correct"]:
        print(f"::warning title=Google Cloud day 53::{ENDPOINT} matched fewer searches than the trigram baseline "
              f"({g['top1_correct']} vs {base['top1_correct']})")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql(sys.argv[2] if len(sys.argv) > 2 else ENDPOINT))
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    else:
        s = summarise(local_scores())
        (HERE / "result.json").write_text(json.dumps(
            {"engine": f"character-trigram cosine (local baseline) / AI.SIMILARITY with {ENDPOINT} and "
                       f"{COMPARE_ENDPOINT} (cloud)", "endpoint": ENDPOINT, **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
