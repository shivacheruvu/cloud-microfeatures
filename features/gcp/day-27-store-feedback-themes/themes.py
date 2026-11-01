"""Day 27 (Google Cloud): one row per store of typed complaint themes with BigQuery's AI.AGG and its output_schema
argument (AI.AGG GA 2026-10-13, output_schema GA 2026-10-14).

  python themes.py                # local: the hand-labelled answer key per store; writes result.json
  python themes.py --sql          # print the BigQuery SQL (comments inlined, no table needed)
  python themes.py --compare OUT  # check bq's JSON output against the answer key (cloud.sh)

The job: 24 customer comments from three stores (data/comments.csv). A store manager wants, per store, the top
complaint as one of six fixed categories, how many comments raise it, a one-line summary and one suggested fix.
AI.AGG reads every comment of a group in one call; output_schema makes the answer a STRUCT with typed, NOT NULL
fields instead of free text that has to be parsed.

The local run can't call Gemini, so the answer key is the hand label in comments.csv. The cloud check is on what must
hold whatever the wording: one STRUCT per store, every field present, top_issue one of the six categories and
mentions a whole number between 1 and the store's comment count. Agreement with the answer key is reported; a
store where Gemini picks another category is a warning (a finding about the model, not a broken run).
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENDPOINT = "gemini-3.5-flash"
CATEGORIES = ["checkout_wait", "out_of_stock", "produce_quality", "price", "staff", "cleanliness"]
FIELDS = ["top_issue", "mentions", "summary", "suggested_fix"]
INSTRUCTION = ("These are customer comments about one grocery store. Find the complaint raised most often. "
               "top_issue must be exactly one of: " + ", ".join(CATEGORIES) + ". mentions is how many of the "
               "comments raise that complaint. Ignore praise.")
OUTPUT_SCHEMA = (
    'top_issue STRING NOT NULL OPTIONS(description = "one of: ' + ", ".join(CATEGORIES) + '"), '
    'mentions INT64 NOT NULL OPTIONS(description = "number of comments that raise top_issue"), '
    'summary STRING NOT NULL OPTIONS(description = "one sentence, under 25 words"), '
    'suggested_fix STRING NOT NULL OPTIONS(description = "one concrete action for the store manager")')


def comments() -> list[dict]:
    with open(HERE / "data" / "comments.csv", newline="") as f:
        return list(csv.DictReader(f))


def answer_key() -> dict[str, dict]:
    out = {}
    rows = comments()
    for store in dict.fromkeys(r["store"] for r in rows):
        mine = [r for r in rows if r["store"] == store]
        issue, n = Counter(r["issue"] for r in mine).most_common(1)[0]
        out[store] = {"comments": len(mine), "top_issue": issue, "mentions": n}
    return out


def _lit(s: str) -> str:
    """A GoogleSQL string literal (JSON escaping is valid GoogleSQL for these characters)."""
    return json.dumps(s, ensure_ascii=False)


def sql() -> str:
    rows = ",\n    ".join(f"STRUCT({_lit(r['store'])} AS store, {_lit(r['comment'])} AS comment)" for r in comments())
    return f"""-- Day 27: per-store complaint themes as a typed STRUCT with AI.AGG(..., output_schema => ...)
-- (AI.AGG GA 2026-10-13, output_schema GA 2026-10-14). TO_JSON_STRING keeps a NULL (an error) visible as "null".
SELECT store,
       COUNT(*) AS comments,
       TO_JSON_STRING(AI.AGG(
         comment,
         {_lit(INSTRUCTION)},
         endpoint => '{ENDPOINT}',
         output_schema => '{OUTPUT_SCHEMA}'
       )) AS themes
FROM UNNEST([
    {rows}
])
GROUP BY store
ORDER BY store
"""


def check(rows: list[dict]) -> tuple[list[str], dict[str, dict]]:
    """Problems that make the run fail, and the parsed themes per store."""
    key, problems, parsed = answer_key(), [], {}
    got = {r.get("store"): r for r in rows}
    if set(got) != set(key):
        problems.append(f"stores back {sorted(map(str, got))}, expected {sorted(key)}")
    for store, r in got.items():
        if store not in key:
            continue
        raw = r.get("themes")
        try:
            t = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            t = None
        if not isinstance(t, dict):
            problems.append(f"{store}: themes is not a STRUCT ({str(raw)[:120]})")
            continue
        missing = [f for f in FIELDS if t.get(f) in (None, "")]
        if missing:
            problems.append(f"{store}: missing {missing}")
            continue
        try:
            mentions = int(t["mentions"])
        except (TypeError, ValueError):
            problems.append(f"{store}: mentions {t['mentions']!r} is not a whole number")
            continue
        if t["top_issue"] not in CATEGORIES:
            problems.append(f"{store}: top_issue {t['top_issue']!r} is not one of the six categories")
        if not 1 <= mentions <= key[store]["comments"]:
            problems.append(f"{store}: mentions {mentions} outside 1..{key[store]['comments']}")
        if int(r.get("comments", key[store]["comments"])) != key[store]["comments"]:
            problems.append(f"{store}: {r.get('comments')} comments grouped, expected {key[store]['comments']}")
        parsed[store] = {**t, "mentions": mentions}
    return problems, parsed


def compare(out_path: str) -> int:
    text = Path(out_path).read_text()
    start = text.find("[")  # bq may print a --scopes warning before the JSON
    try:
        rows = json.loads(text[start:]) if start >= 0 else []
    except ValueError as e:
        print(f"::error title=Unreadable BigQuery output::{e}")
        return 1
    problems, parsed = check(rows)
    if problems:
        print("::error title=Google Cloud day 27 output check failed::" + "; ".join(problems)[:900])
        return 1
    key = answer_key()
    agree = [s for s in key if parsed[s]["top_issue"] == key[s]["top_issue"]]
    within = [s for s in agree if abs(parsed[s]["mentions"] - key[s]["mentions"]) <= 1]
    detail = "; ".join(f"{s}: {parsed[s]['top_issue']} x{parsed[s]['mentions']} (key {key[s]['top_issue']} "
                       f"x{key[s]['mentions']}), fix: {parsed[s]['suggested_fix'][:80]}" for s in key)
    print(f"::notice title=Google Cloud day 27::AI.AGG ({ENDPOINT}) with output_schema returned one typed STRUCT per "
          f"store for {len(parsed)} stores ({sum(v['comments'] for v in key.values())} comments, one "
          f"query): top issue agrees with the answer key for {len(agree)} of {len(key)}, mention count within 1 for "
          f"{len(within)}. {detail}"[:1000])
    if len(agree) < len(key):
        print(f"::warning title=Google Cloud day 27::{ENDPOINT} picked a different top issue than the answer key for "
              + ", ".join(s for s in key if s not in agree))
    return 0


def summarise() -> dict:
    key = answer_key()
    return {"endpoint": ENDPOINT, "stores": len(key), "comments": sum(v["comments"] for v in key.values()),
            "categories": CATEGORIES, "output_fields": FIELDS, "answer_key": key, "queries": 1}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql())
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2]))
    else:
        s = summarise()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": f"hand-labelled answer key (local) / AI.AGG with output_schema on {ENDPOINT} (cloud)", **s},
            indent=2) + "\n")
        print(json.dumps(s, indent=2))
