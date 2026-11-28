"""Day 54 (Google Cloud): triage shopper reviews for product-quality problems with **AI.IF in optimized mode**
(`optimization_mode => 'MINIMIZE_COST'`, GA, BigQuery release note 2026-11-17). In MINIMIZE_COST mode BigQuery
answers most rows with a local, distilled model and sends only the rest to Gemini; MAXIMIZE_QUALITY sends every row
to Gemini. One query runs both modes over the same 40 reviews so their answers can be compared row by row.

  python triage.py                  # local: answer key + keyword baseline; writes result.json
  python triage.py --sql N          # print the BigQuery SQL for call form N (reviews inlined, no table needed)
  python triage.py --compare OUT N  # check bq's JSON output against the answer key (cloud.sh)

Call forms (cloud.sh tries them in order and names the accepted one; the argument and its two values come from
BigQuery DataFrames' `bbq.ai.if_(optimization_mode=...)`, which compiles to `optimization_mode => 'MINIMIZE_COST'`):
  1. one AI.IF column per mode: MINIMIZE_COST and MAXIMIZE_QUALITY
  2. MINIMIZE_COST only (in case only the quality column is refused)

Must hold (else the run fails): 40 rows back, and every MINIMIZE_COST answer is a BOOL (NULL counts as a failure of
that row and is reported, more than 4 NULLs fails the run). Reported: accuracy / precision / recall of each mode
against the answer key, how often the two modes agree, and the keyword baseline. Accuracy under 0.85 for
MINIMIZE_COST is a warning (a finding about the model, not a broken run).
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_ACCURACY = 0.85
MAX_NULLS = 4
QUESTION = ("Does this shopper review say the item they received was in bad condition or wrong: spoiled, moldy, "
            "expired, stale, damaged, crushed, leaking, thawed, short on quantity, or a different item than ordered? "
            "Complaints about price, taste preference, delivery time or service do not count.")
FORMS = {1: "MINIMIZE_COST and MAXIMIZE_QUALITY side by side", 2: "MINIMIZE_COST only"}
# Local baseline: a generic complaint-word list of the kind a rule-based triage queue starts with. It was fixed
# before looking at model output and is NOT tuned to these 40 reviews (prefix match: "mold" also hits "moldy").
KEYWORDS = ("bad", "broken", "damage", "spoil", "rotten", "expire", "mold", "leak", "wrong", "missing", "stale",
            "late", "rude", "refund", "pricey", "smell")


def reviews() -> list[dict]:
    with open(HERE / "data" / "reviews.csv", newline="") as f:
        return [{"review_no": int(r["review_no"]), "product": r["product"], "review": r["review"],
                 "problem": r["problem"] == "true"} for r in csv.DictReader(f)]


def keyword_flag(text: str) -> bool:
    words = re.findall(r"[a-z]+", text.lower())
    return any(w == k or (len(k) > 3 and w.startswith(k)) for w in words for k in KEYWORDS)


def _lit(s: str) -> str:
    """A GoogleSQL string literal (JSON escaping is valid GoogleSQL for these characters)."""
    return json.dumps(s, ensure_ascii=False)


def sql(form: int) -> str:
    if form not in FORMS:
        raise ValueError(form)
    rows = ",\n    ".join(
        f"STRUCT({r['review_no']} AS review_no, {_lit(r['product'] + ': ' + r['review'])} AS review)"
        for r in reviews())
    prompt = f"(CONCAT('Shopper review of ', review), {_lit(' ' + QUESTION)})"
    quality = ("NULL" if form == 2 else
               f"AI.IF(prompt => {prompt}, optimization_mode => 'MAXIMIZE_QUALITY')")
    return f"""-- Day 54: shopper-review triage with AI.IF in optimized mode (optimization_mode, GA 2026-11-17),
-- call form {form} ({FORMS[form]}).
SELECT review_no,
       AI.IF(prompt => {prompt}, optimization_mode => 'MINIMIZE_COST') AS cost,
       {quality} AS quality
FROM UNNEST([
    {rows}
])
ORDER BY review_no
"""


# ---------- cloud output
def _redact(text: str) -> str:
    return re.sub(r"\d{6,}", "<number>", text.replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>"))


def _bool(v):
    """bq --format=json gives BOOL as "true"/"false" strings; None stays None; anything else is invalid."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    raise ValueError(v)


def score(pred: dict[int, bool | None]) -> dict:
    key = {r["review_no"]: r["problem"] for r in reviews()}
    tp = sum(1 for n, k in key.items() if k and pred.get(n) is True)
    fp = sum(1 for n, k in key.items() if not k and pred.get(n) is True)
    fn = sum(1 for n, k in key.items() if k and pred.get(n) is not True)
    right = sum(1 for n, k in key.items() if pred.get(n) is k)
    return {"accuracy": round(right / len(key), 3),
            "precision": round(tp / (tp + fp), 3) if tp + fp else 0.0,
            "recall": round(tp / (tp + fn), 3) if tp + fn else 0.0,
            "flagged": sum(1 for v in pred.values() if v is True),
            "nulls": sum(1 for n in key if pred.get(n) is None),
            "wrong": sorted(n for n, k in key.items() if pred.get(n) is not None and pred.get(n) is not k)}


def check(rows: list[dict], form: int) -> tuple[list[str], dict, dict]:
    key = {r["review_no"] for r in reviews()}
    problems, cost, quality = [], {}, {}
    for r in rows:
        try:
            n = int(r.get("review_no"))
            cost[n] = _bool(r.get("cost"))
            quality[n] = _bool(r.get("quality")) if form == 1 else None
        except (TypeError, ValueError) as e:
            problems.append(f"row {_redact(str(r.get('review_no')))[:20]}: not a BOOL ({_redact(str(e))[:80]})")
    if sorted(cost) != sorted(key):
        problems.append(f"{len(cost)} reviews back, expected {len(key)}")
    nulls = sum(1 for n in key if cost.get(n) is None)
    if nulls > MAX_NULLS:
        problems.append(f"MINIMIZE_COST returned NULL for {nulls} reviews (more than {MAX_NULLS})")
    return problems, cost, quality


def read_rows(path: str) -> list[dict] | None:
    text = Path(path).read_text()
    dec = json.JSONDecoder()
    for i in (k for k, ch in enumerate(text) if ch == "["):
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, list) and all(isinstance(r, dict) for r in obj):
            return obj
    return None


def compare(out_path: str, form: int) -> int:
    rows = read_rows(out_path)
    if rows is None:
        print(f"::error title=Unreadable BigQuery output::{_redact(Path(out_path).read_text()[:300])!r}")
        return 1
    problems, cost, quality = check(rows, form)
    if problems:
        print("::error title=Google Cloud day 54 output check failed::" + "; ".join(problems)[:900])
        return 1
    c = score(cost)
    base = local_summary()["keyword_baseline"]
    msg = (f"AI.IF optimization_mode => 'MINIMIZE_COST' triaged 40 shopper reviews (form {form}: {FORMS[form]}): "
           f"accuracy {c['accuracy']}, precision {c['precision']}, recall {c['recall']}, {c['flagged']} flagged, "
           f"{c['nulls']} NULL; wrong: {', '.join(map(str, c['wrong'])) or 'none'}.")
    if form == 1:
        q = score(quality)
        agree = sum(1 for n in cost if cost[n] is not None and cost[n] is quality.get(n))
        msg += (f" MAXIMIZE_QUALITY: accuracy {q['accuracy']}, precision {q['precision']}, recall {q['recall']}, "
                f"{q['nulls']} NULL; wrong: {', '.join(map(str, q['wrong'])) or 'none'}. "
                f"The two modes agree on {agree}/40.")
    msg += f" Keyword baseline: accuracy {base['accuracy']}."
    print(f"::notice title=Google Cloud day 54::{msg}")
    if c["accuracy"] < MIN_ACCURACY:
        print(f"::warning title=Google Cloud day 54::MINIMIZE_COST accuracy {c['accuracy']} is under {MIN_ACCURACY}")
    return 0


def local_summary() -> dict:
    rows = reviews()
    base = score({r["review_no"]: keyword_flag(r["product"] + ": " + r["review"]) for r in rows})
    return {"reviews": len(rows), "problems_in_key": sum(r["problem"] for r in rows),
            "keyword_baseline": base, "min_accuracy": MIN_ACCURACY, "queries": 1, "forms": FORMS}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql(int(sys.argv[2]) if len(sys.argv) > 2 else 1))
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2], int(sys.argv[3])))
    else:
        s = local_summary()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "answer key + keyword baseline (local) / AI.IF optimization_mode MINIMIZE_COST vs "
                       "MAXIMIZE_QUALITY (cloud)", **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
