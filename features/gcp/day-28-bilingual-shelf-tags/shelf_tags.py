"""Day 28 (Google Cloud): bilingual (English/Spanish) shelf tags with BigQuery's AI.TRANSLATE (GA 2026-10-22), and a
check that every price, size and count on the tag survives translation.

  python shelf_tags.py                # local: runs the checks on the hand-written Spanish reference; writes result.json
  python shelf_tags.py --sql          # print the BigQuery SQL (tags inlined, no table needed)
  python shelf_tags.py --compare OUT  # check bq's JSON output (cloud.sh)

The job: a store in a bilingual neighbourhood prints each shelf tag in English and Spanish. AI.TRANSLATE translates
12 tags in one query (Cloud Translation NMT, the default model), with return_details => TRUE so each row carries a
status instead of a silent NULL. A translated tag is only usable if the numbers on it are unchanged: the price, the
size and the multi-buy count. So the check compares the digit groups of each tag before and after (so "$0.59" and
"$0,59" both pass: the digits are the same, only the separator moved), and that the product word is there
(a keyword list per item, from data/shelf_tags.csv).

The run fails only if the output is broken (a missing row, a NULL result, a status that isn't OK). A tag whose numbers
changed or whose product word is missing is a warning: a finding about the translation, listed for a human to fix.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET, SOURCE = "es", "en"


def tags() -> list[dict]:
    with open(HERE / "data" / "shelf_tags.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def digit_groups(text: str) -> list[str]:
    return sorted(re.findall(r"\d+", text or ""))


def judge(tag: dict, translated: str) -> dict:
    t = (translated or "").lower()
    return {"sku": tag["sku"], "tag": tag["tag"], "es": translated,
            "numbers_kept": digit_groups(tag["tag"]) == digit_groups(translated),
            "product_word": any(k in t for k in tag["es_keywords"].split("|")),
            "changed": t.strip() != tag["tag"].lower().strip()}


def _lit(s: str) -> str:
    """A GoogleSQL string literal (JSON escaping is valid GoogleSQL for these characters)."""
    return json.dumps(s, ensure_ascii=False)


def sql() -> str:
    rows = ",\n    ".join(f"STRUCT({_lit(r['sku'])} AS sku, {_lit(r['tag'])} AS tag)" for r in tags())
    return f"""-- Day 28: bilingual shelf tags with AI.TRANSLATE (GA 2026-10-22), default NMT model.
-- return_details => TRUE returns STRUCT(result, status, ...); TO_JSON_STRING keeps it readable in bq's JSON output.
SELECT sku, tag,
       TO_JSON_STRING(AI.TRANSLATE(tag, '{TARGET}', source_language => '{SOURCE}', return_details => TRUE)) AS es
FROM UNNEST([
    {rows}
])
ORDER BY sku
"""


def check(rows: list[dict]) -> tuple[list[str], list[dict]]:
    """Problems that make the run fail, and the judged rows."""
    by_sku = {t["sku"]: t for t in tags()}
    got = {r.get("sku"): r for r in rows}
    problems, judged = [], []
    if set(got) != set(by_sku):
        problems.append(f"skus back {sorted(map(str, got))}, expected {sorted(by_sku)}")
    for sku, tag in by_sku.items():
        r = got.get(sku)
        if r is None:
            continue
        raw = r.get("es")
        try:
            d = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            d = None
        if not isinstance(d, dict):
            problems.append(f"{sku}: AI.TRANSLATE returned no STRUCT ({str(raw)[:120]})")
            continue
        if str(d.get("status", "")).upper() != "OK" or not d.get("result"):
            problems.append(f"{sku}: status {str(d.get('status'))[:200]!r}, result {str(d.get('result'))[:40]!r}")
            continue
        judged.append(judge(tag, d["result"]))
    return problems, judged


def tally(judged: list[dict]) -> dict:
    return {"tags": len(judged), "numbers_kept": sum(j["numbers_kept"] for j in judged),
            "product_word": sum(j["product_word"] for j in judged), "changed": sum(j["changed"] for j in judged)}


def compare(out_path: str) -> int:
    text = Path(out_path).read_text(encoding="utf-8")
    start = text.find("[")  # bq may print a --scopes warning before the JSON
    try:
        rows = json.loads(text[start:]) if start >= 0 else []
    except ValueError as e:
        print(f"::error title=Unreadable BigQuery output::{e}")
        return 1
    problems, judged = check(rows)
    if problems:
        print("::error title=Google Cloud day 28 output check failed::" + "; ".join(problems)[:900])
        return 1
    t = tally(judged)
    sample = "; ".join(f"{j['tag']} -> {j['es']}" for j in judged[:4])
    print(f"::notice title=Google Cloud day 28::AI.TRANSLATE (NMT, en->es) translated all {t['tags']} shelf tags in "
          f"one query, every status OK: numbers kept on {t['numbers_kept']} of {t['tags']}, product word present on "
          f"{t['product_word']}, changed from English on {t['changed']}. {sample}"[:1000])
    bad = [j for j in judged if not (j["numbers_kept"] and j["product_word"] and j["changed"])]
    if bad:
        print("::warning title=Google Cloud day 28 tags to review::" + "; ".join(
            f"{j['sku']} {j['tag']!r} -> {j['es']!r}" for j in bad)[:900])
    return 0


def summarise() -> dict:
    judged = [judge(t, t["es_reference"]) for t in tags()]
    return {"target_language": TARGET, "source_language": SOURCE, "model": "nmt (default)", "queries": 1,
            "reference_check": tally(judged)}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql())
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2]))
    else:
        s = summarise()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "hand-written Spanish reference (local) / AI.TRANSLATE NMT in BigQuery (cloud)", **s},
            indent=2, ensure_ascii=False) + "\n")
        print(json.dumps(s, indent=2, ensure_ascii=False))
