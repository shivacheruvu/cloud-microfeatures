"""Day 26 (Google Cloud): match abbreviated receipt lines to catalog products with BigQuery's AI.SIMILARITY on the
gemini-embedding-2 model (GA in BigQuery's embedding functions, 2026-10-05).

  python matcher.py                # local: character-trigram cosine similarity as the baseline; writes result.json
  python matcher.py --sql          # print the BigQuery SQL (lines and products inlined, no table needed)
  python matcher.py --compare OUT  # check bq's JSON output and report Gemini's matches next to the baseline (cloud.sh)

The job: receipts print lines like "GRND BF 80/20" or "OJ NO PULP 52OZ". To roll basket data up by product, each
line has to be matched to one of 12 catalog products. Every (line, product) pair gets a similarity score; a line's
match is the product with the highest score. An answer key (data/receipt_lines.csv) says which product is right.

The local run can't reproduce Gemini embeddings, so locally the score is a character-trigram cosine (what a plain
fuzzy matcher would do). The cloud check is on what must hold whatever the exact scores: all 144 pairs come back
with a score in [-1, 1]. Gemini's top-1 accuracy is reported next to the baseline's; if it is lower, that is a
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
ENDPOINT = "gemini-embedding-2"


def _csv(name: str) -> list[dict]:
    with open(HERE / "data" / name, newline="") as f:
        return list(csv.DictReader(f))


def catalog() -> list[dict]:
    return _csv("catalog.csv")


def lines() -> list[dict]:
    return _csv("receipt_lines.csv")


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
    return {(ln["line"], p["barcode"]): trigram_cosine(ln["line"], p["name"]) for ln in lines() for p in catalog()}


# ---------------------------------------------------------------- matching and summary
def matches(scores: dict[tuple[str, str], float]) -> dict[str, dict]:
    """Per line: the best product, its score and the margin over the runner-up (ties go to the lower barcode)."""
    out = {}
    for ln in lines():
        ranked = sorted(((scores[(ln["line"], p["barcode"])], p["barcode"]) for p in catalog()),
                        key=lambda s: (-s[0], s[1]))
        (best, code), (second, _) = ranked[0], ranked[1]
        out[ln["line"]] = {"barcode": code, "score": round(best, 4), "margin": round(best - second, 4),
                           "correct": code == ln["expected_barcode"]}
    return out


def summarise(scores: dict[tuple[str, str], float]) -> dict:
    m = matches(scores)
    names = {p["barcode"]: p["name"] for p in catalog()}
    right = [ln for ln, r in m.items() if r["correct"]]
    return {
        "pairs_scored": len(scores),
        "lines": len(m),
        "top1_correct": len(right),
        "top1_accuracy_pct": round(100 * len(right) / len(m), 1),
        "mean_margin": round(sum(r["margin"] for r in m.values()) / len(m), 4),
        "wrong": {ln: names[r["barcode"]] for ln, r in m.items() if not r["correct"]},
    }


# ---------------------------------------------------------------- BigQuery
def sql() -> str:
    ls = ", ".join(json.dumps(ln["line"]) for ln in lines())
    ps = ",\n    ".join(f"STRUCT({json.dumps(p['barcode'])} AS barcode, {json.dumps(p['name'])} AS name)"
                       for p in catalog())
    return f"""-- Day 26: score every (receipt line, catalog product) pair with AI.SIMILARITY on {ENDPOINT}
-- (GA in BigQuery's embedding functions 2026-10-05). TO_JSON_STRING keeps a NULL (an error) visible as "null".
SELECT line, barcode,
       TO_JSON_STRING(AI.SIMILARITY(content1 => line, content2 => name, endpoint => '{ENDPOINT}')) AS raw
FROM UNNEST([{ls}]) AS line
CROSS JOIN UNNEST([
    {ps}
])
ORDER BY line, barcode
"""


def score_from_json(raw) -> float | None:
    v = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(v, dict):
        v = v.get("result", v.get("similarity"))
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def compare(out_path: str) -> int:
    text = Path(out_path).read_text()
    start = text.find("[")  # bq may print a --scopes warning before the JSON
    try:
        rows = json.loads(text[start:]) if start >= 0 else []
    except ValueError as e:
        print(f"::error title=Unreadable BigQuery output::{e}")
        return 1
    cloud = {(r["line"], r["barcode"]): score_from_json(r.get("raw")) for r in rows}
    local = local_scores()
    problems = []
    if set(cloud) != set(local):
        problems.append(f"{len(cloud)} pairs back, expected {len(local)}")
    bad = [k for k, s in cloud.items() if s is None or not -1.0001 <= s <= 1.0001]
    if bad:
        sample = next((r.get("raw") for r in rows if (r["line"], r["barcode"]) == bad[0]), None)
        problems.append(f"{len(bad)} pairs without a score in [-1, 1] (first raw value: {str(sample)[:200]})")
    if problems:
        print("::error title=Google Cloud day 26 differs from local::" + "; ".join(problems))
        return 1
    c, base = summarise(cloud), summarise(local)
    print(f"::notice title=Google Cloud day 26::AI.SIMILARITY ({ENDPOINT}) scored all {c['pairs_scored']} "
          f"receipt-line x product pairs: top-1 match right for {c['top1_correct']} of {c['lines']} lines "
          f"({c['top1_accuracy_pct']}%) vs {base['top1_correct']} of {base['lines']} for the local trigram baseline; "
          f"mean margin over the runner-up {c['mean_margin']}. Wrong: {json.dumps(c['wrong'])[:300]}")
    if c["top1_correct"] < base["top1_correct"]:
        print(f"::warning title=Google Cloud day 26::{ENDPOINT} matched fewer lines than the trigram baseline "
              f"({c['top1_correct']} vs {base['top1_correct']})")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql())
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2]))
    else:
        s = summarise(local_scores())
        (HERE / "result.json").write_text(json.dumps(
            {"engine": f"character-trigram cosine (local baseline) / AI.SIMILARITY with {ENDPOINT} (cloud)",
             "endpoint": ENDPOINT, **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
