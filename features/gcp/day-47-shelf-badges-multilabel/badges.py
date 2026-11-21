"""Day 47 (Google Cloud): shelf diet badges with **multi-label AI.CLASSIFY** (`output_mode => 'multi'`, GA, BigQuery
release note 2026-11-17). One product can carry several badges (hummus is vegan, vegetarian, gluten-free and
dairy-free) or none (a chicken caesar wrap); a single-label classifier must pick exactly one.

  python badges.py                  # local: the hand-worked answer key; writes result.json
  python badges.py --sql N          # print the BigQuery SQL for call form N (products inlined, no table needed)
  python badges.py --compare OUT N  # check bq's JSON output against the answer key (cloud.sh)

Call forms (cloud.sh tries them in order and names the accepted one; the upstream google/skills reference documents
`output_mode => 'multi'` returning ARRAY<STRING> and categories as labels or (label, description) pairs):
  1. categories => [('vegan', 'no animal products ...'), ...], output_mode => 'multi'
  2. categories => ['vegan', ...], output_mode => 'multi', with the badge definitions prefixed to the input
  3. form 2 without the single-label baseline column (in case only the baseline is refused)
The same query also runs the default single-label AI.CLASSIFY (returns one STRING) on the same input as a baseline.

Must hold (else the run fails): 14 rows back; every multi-label answer is an array (empty allowed) whose labels all
come from the five badges. Reported: badge-level precision / recall / F1 against the answer key, products with exactly
the right badge set, and how many badges single-label could ever surface (at most one per product). F1 below 0.8 is a
warning (a finding about the model, not a broken run).
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_F1 = 0.8
BADGES = {  # label -> definition (also the description passed to AI.CLASSIFY in form 1)
    "vegan": "no animal products at all: no meat, fish, milk, butter, cheese, eggs or honey",
    "vegetarian": "no meat and no fish; milk, cheese, eggs and honey are allowed",
    "gluten_free": "no wheat, barley, rye or oats, unless the oats are certified gluten-free",
    "dairy_free": "no milk, butter, cream, cheese, yogurt or whey",
    "contains_nuts": "contains peanuts or tree nuts such as almonds, cashews, pecans; coconut is not a nut",
}
FORMS = {1: "labels with descriptions", 2: "plain labels, definitions in the input",
         3: "plain labels, definitions in the input, no single-label baseline"}


def products() -> list[dict]:
    with open(HERE / "data" / "products.csv", newline="") as f:
        return [{"product_no": int(r["product_no"]), "product": r["product"], "ingredients": r["ingredients"],
                 "badges": sorted(b for b in r["badges"].split(";") if b)} for r in csv.DictReader(f)]


def _lit(s: str) -> str:
    """A GoogleSQL string literal (JSON escaping is valid GoogleSQL for these characters)."""
    return json.dumps(s, ensure_ascii=False)


def _definitions() -> str:
    return "Badge definitions: " + "; ".join(f"{k}: {v}" for k, v in BADGES.items()) + ". "


def sql(form: int) -> str:
    if form not in FORMS:
        raise ValueError(form)
    rows = ",\n    ".join(
        f"STRUCT({r['product_no']} AS product_no, {_lit(r['product'] + ': ' + r['ingredients'])} AS item)"
        for r in products())
    labels = ", ".join(_lit(k) for k in BADGES)
    if form == 1:
        cats = "[" + ", ".join(f"({_lit(k)}, {_lit(v)})" for k, v in BADGES.items()) + "]"
        text = "CONCAT('Grocery product and its ingredients: ', item)"
    else:
        cats = f"[{labels}]"
        text = f"CONCAT({_lit(_definitions())}, 'Grocery product and its ingredients: ', item)"
    baseline = ("NULL" if form == 3 else
                f"TO_JSON_STRING(AI.CLASSIFY({text}, categories => {cats}))")
    return f"""-- Day 47: shelf diet badges with multi-label AI.CLASSIFY (output_mode => 'multi', GA 2026-11-17),
-- call form {form} ({FORMS[form]}), next to the default single-label AI.CLASSIFY on the same input.
SELECT product_no,
       TO_JSON_STRING(AI.CLASSIFY({text}, categories => {cats}, output_mode => 'multi')) AS multi,
       {baseline} AS single
FROM UNNEST([
    {rows}
])
ORDER BY product_no
"""


# ---------- cloud output
def _redact(text: str) -> str:
    return re.sub(r"\d{6,}", "<number>", text.replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>"))


def _labels(raw):
    """JSON array of strings -> sorted list (or None if it isn't one); labels are compared case-insensitively."""
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if v is None:
        return None
    if isinstance(v, str):
        return None
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        return None
    return sorted({x.strip().lower().replace("-", "_").replace(" ", "_") for x in v})


def _single(raw):
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        v = raw
    if isinstance(v, list) and len(v) == 1:
        v = v[0]
    return v.strip().lower().replace("-", "_").replace(" ", "_") if isinstance(v, str) and v.strip() else None


def check(rows: list[dict]) -> tuple[list[str], dict[int, list[str]], dict[int, str | None]]:
    key = {r["product_no"]: r for r in products()}
    problems, got = [], {}
    for r in rows:
        try:
            got[int(r.get("product_no"))] = r
        except (TypeError, ValueError):
            problems.append(f"bad product_no {r.get('product_no')!r}")
    if sorted(got) != sorted(key):
        problems.append(f"products back {sorted(got)}, expected 1..{len(key)}")
    multi, single = {}, {}
    for n in key:
        if n not in got:
            continue
        labels = _labels(got[n].get("multi"))
        if labels is None:
            problems.append(f"product {n}: multi-label answer is not an array of labels: "
                            f"{_redact(str(got[n].get('multi')))[:120]}")
            continue
        unknown = [x for x in labels if x not in BADGES]
        if unknown:
            problems.append(f"product {n}: labels outside the badge set: {', '.join(unknown)[:120]}")
        multi[n] = labels
        single[n] = _single(got[n].get("single"))
    return problems, multi, single


def score(multi: dict[int, list[str]], single: dict[int, str | None]) -> dict:
    key = {r["product_no"]: set(r["badges"]) for r in products()}
    tp = fp = fn = 0
    exact, missed, extra = 0, [], []
    for n, k in key.items():
        g = set(multi.get(n) or [])
        tp += len(g & k); fp += len(g - k); fn += len(k - g)
        exact += g == k
        missed += [f"{n}:{b}" for b in sorted(k - g)]
        extra += [f"{n}:{b}" for b in sorted(g - k)]
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    total = sum(len(k) for k in key.values())
    single_right = sum(1 for n, k in key.items() if single.get(n) in k)
    return {"badges_in_key": total, "true_positive": tp, "false_positive": fp, "false_negative": fn,
            "precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3),
            "products_exact": exact, "products": len(key), "missed": missed, "extra": extra,
            "single_label_correct": single_right, "single_label_recall": round(single_right / total, 3)}


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
    problems, multi, single = check(rows)
    if problems:
        print("::error title=Google Cloud day 47 output check failed::" + "; ".join(problems)[:900])
        return 1
    s = score(multi, single)
    print(f"::notice title=Google Cloud day 47::multi-label AI.CLASSIFY (form {form}: {FORMS[form]}) tagged "
          f"{s['products']} products with diet badges in one query: precision {s['precision']}, recall {s['recall']}, "
          f"F1 {s['f1']} over {s['badges_in_key']} badges; {s['products_exact']}/{s['products']} products with exactly "
          f"the right set; missed: {', '.join(s['missed']) or 'none'}; extra: {', '.join(s['extra']) or 'none'}. "
          + ("Single-label baseline not run (form 3)." if form == 3 else
             f"Single-label AI.CLASSIFY on the same input: {s['single_label_correct']}/{s['badges_in_key']} badges "
             f"(recall {s['single_label_recall']})."))
    if s["f1"] < MIN_F1:
        print(f"::warning title=Google Cloud day 47::multi-label F1 {s['f1']} is under {MIN_F1}")
    return 0


def summarise() -> dict:
    rows = products()
    total = sum(len(r["badges"]) for r in rows)
    return {"products": len(rows), "badges": list(BADGES), "badges_in_key": total,
            "per_badge": {b: sum(b in r["badges"] for r in rows) for b in BADGES},
            "products_with_no_badge": sum(not r["badges"] for r in rows),
            "products_with_3_plus_badges": sum(len(r["badges"]) >= 3 for r in rows),
            "single_label_max_recall": round(sum(1 for r in rows if r["badges"]) / total, 3),
            "min_f1": MIN_F1, "queries": 1, "model_calls": 2 * len(rows),
            "answer_key": {str(r["product_no"]): {"product": r["product"], "badges": r["badges"]} for r in rows}}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql(int(sys.argv[2]) if len(sys.argv) > 2 else 1))
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2], int(sys.argv[3])))
    else:
        s = summarise()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "hand-worked answer key (local) / AI.CLASSIFY output_mode => 'multi' (cloud)", **s},
            indent=2) + "\n")
        print(json.dumps({k: v for k, v in s.items() if k != "answer_key"}, indent=2))
