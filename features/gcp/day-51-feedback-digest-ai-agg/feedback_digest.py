"""Day 51 (Google Cloud): a typed post-Thanksgiving feedback digest per department with BigQuery's AI.AGG and its
output_schema argument (structured STRUCT output from an aggregate; GA 2026-10-14).

  python feedback_digest.py                # local: the hand-labelled comments and the answer key; writes result.json
  python feedback_digest.py --sql          # print the BigQuery SQL (comments inlined, no table needed)
  python feedback_digest.py --compare OUT  # check bq's JSON output against the answer key (cloud.sh)

The job: 24 shopper comments from Thanksgiving week, six each for produce, bakery, dairy and meat. One query groups
them by department and asks AI.AGG for a STRUCT per group: top_issue (out_of_stock, price, wait_time, quality, staff or
none), sentiment (positive, mixed, negative), any_out_of_stock (BOOL) and a one-line headline. Exact counts come from
SQL's COUNT(*), not from the model (AI.AGG batches, so any count it states is a reconstruction).

Answer key: every comment is hand-labelled (issue, out_of_stock, sentiment). Per department, top_issue is the issue
raised by the most shoppers, or none when fewer than two complain; any_out_of_stock is true if one comment says
something was sold out; sentiment is the majority label. The traps: produce lists six different sold-out items (one
issue, not six), and meat is glowing except one shopper whose 20 lb turkeys were sold out (top_issue none, but
any_out_of_stock true).

Must hold (else the run fails): four departments back, six comments each, and every digest is a STRUCT whose
top_issue and sentiment are allowed values, any_out_of_stock is a BOOL and headline is non-empty. Reported: fields equal
to the answer key (12 = 4 x 3). A difference is a warning (a finding about the model, not a broken run).
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ISSUES = ["out_of_stock", "price", "wait_time", "quality", "staff", "none"]
SENTIMENTS = ["positive", "mixed", "negative"]
FIELDS = ["top_issue", "sentiment", "any_out_of_stock"]
INSTRUCTION = (
    "These are shopper comments about one grocery store department from Thanksgiving week. "
    "top_issue is the complaint raised by the most shoppers: out_of_stock (an item sold out or missing), price, "
    "wait_time (queues or slow service), quality, or staff. Several different sold-out items are all out_of_stock. "
    "Use none when fewer than two shoppers complain about anything. sentiment is the mood of most comments. "
    "any_out_of_stock is true if at least one comment says an item was sold out or missing. "
    "headline is one short sentence for the store manager.")
OUTPUT_SCHEMA = (
    'top_issue STRING NOT NULL OPTIONS(description = "one of out_of_stock, price, wait_time, quality, staff, none"), '
    'sentiment STRING NOT NULL OPTIONS(description = "one of positive, mixed, negative"), '
    'any_out_of_stock BOOL NOT NULL OPTIONS(description = "true if any comment reports a sold-out item"), '
    'headline STRING NOT NULL OPTIONS(description = "one sentence for the store manager")')


def comments() -> list[dict]:
    with open(HERE / "data" / "comments.csv", newline="") as f:
        return [{**r, "comment_no": int(r["comment_no"]), "out_of_stock": r["out_of_stock"] == "true"}
                for r in csv.DictReader(f)]


def answer_key() -> dict[str, dict]:
    by: dict[str, list[dict]] = {}
    for c in comments():
        by.setdefault(c["department"], []).append(c)
    key = {}
    for dept, cs in sorted(by.items()):
        issues = Counter(c["issue"] for c in cs if c["issue"] != "none").most_common()
        top = issues[0][0] if issues and issues[0][1] >= 2 else "none"
        if len(issues) > 1 and issues[0][1] == issues[1][1]:
            raise ValueError(f"{dept}: tied top issue, the key would be ambiguous")
        moods = Counter(c["sentiment"] for c in cs).most_common()
        key[dept] = {"comments": len(cs), "top_issue": top, "sentiment": moods[0][0],
                     "any_out_of_stock": any(c["out_of_stock"] for c in cs)}
    return key


def _lit(s: str) -> str:
    """A GoogleSQL string literal (JSON escaping is valid GoogleSQL for these characters)."""
    return json.dumps(s, ensure_ascii=False)


def sql() -> str:
    rows = ",\n    ".join(f"STRUCT({c['comment_no']} AS comment_no, {_lit(c['department'])} AS department, "
                          f"{_lit(c['comment'])} AS comment)" for c in comments())
    return f"""-- Day 51: one typed digest per department with AI.AGG and output_schema (GA 2026-10-14).
-- Counts come from COUNT(*); the model only labels and summarises.
SELECT department, comments, TO_JSON_STRING(digest) AS digest
FROM (
  SELECT department,
         COUNT(*) AS comments,
         AI.AGG(comment, {_lit(INSTRUCTION)},
                output_schema => '{OUTPUT_SCHEMA}') AS digest
  FROM UNNEST([
    {rows}
  ])
  GROUP BY department
)
ORDER BY department
"""


def _parse(raw) -> dict | None:
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return None


def clean(d: dict | None) -> dict | None:
    """A usable digest (normalised labels) or None."""
    if not d:
        return None
    issue = str(d.get("top_issue") or "").strip().lower().replace(" ", "_").replace("-", "_")
    mood = str(d.get("sentiment") or "").strip().lower()
    oos = _bool(d.get("any_out_of_stock"))
    headline = str(d.get("headline") or "").strip()
    if issue not in ISSUES or mood not in SENTIMENTS or oos is None or not headline:
        return None
    return {"top_issue": issue, "sentiment": mood, "any_out_of_stock": oos, "headline": headline}


def check(rows: list[dict]) -> tuple[list[str], dict[str, dict | None]]:
    """Problems that make the run fail, and the cleaned digest per department."""
    key = answer_key()
    problems, got = [], {}
    for r in rows:
        got[str(r.get("department"))] = r
    if sorted(got) != sorted(key):
        problems.append(f"departments back {sorted(got)}, expected {sorted(key)}")
    digests: dict[str, dict | None] = {}
    for dept, k in key.items():
        if dept not in got:
            continue
        try:
            n = int(got[dept].get("comments"))
        except (TypeError, ValueError):
            n = None
        if n != k["comments"]:
            problems.append(f"{dept}: COUNT(*) {got[dept].get('comments')!r}, expected {k['comments']}")
        raw = _parse(got[dept].get("digest"))
        digests[dept] = clean(raw)
        if raw is None:
            problems.append(f"{dept}: no digest STRUCT")
        elif digests[dept] is None:
            problems.append(f"{dept}: unusable digest {json.dumps(raw)[:160]}")
    return problems, digests


def score(digests: dict[str, dict | None]) -> dict:
    key = answer_key()
    right, wrong = 0, {}
    for dept, k in key.items():
        d = digests.get(dept) or {}
        misses = [f for f in FIELDS if d.get(f) != k[f]]
        right += len(FIELDS) - len(misses)
        if misses:
            wrong[dept] = {f: d.get(f) for f in misses}
    return {"fields_right": right, "fields": len(key) * len(FIELDS),
            "departments_right": len(key) - len(wrong), "departments": len(key), "wrong": wrong}


def compare(out_path: str) -> int:
    text = Path(out_path).read_text()
    start = text.find("[")  # bq may print a --scopes warning before the JSON
    try:
        rows = json.loads(text[start:]) if start >= 0 else []
    except ValueError as e:
        print(f"::error title=Unreadable BigQuery output::{e}")
        return 1
    problems, digests = check(rows)
    if problems:
        print("::error title=Google Cloud day 51 output check failed::" + "; ".join(problems)[:900])
        return 1
    s = score(digests)
    per = "; ".join(f"{d}: {v['top_issue']}, {v['sentiment']}, sold-out {'yes' if v['any_out_of_stock'] else 'no'}"
                    for d, v in digests.items())
    print(f"::notice title=Google Cloud day 51::AI.AGG with output_schema returned a typed digest for "
          f"{s['departments']} departments ({sum(k['comments'] for k in answer_key().values())} comments) in one "
          f"query: {s['fields_right']}/{s['fields']} fields and {s['departments_right']}/{s['departments']} "
          f"departments match the answer key. {per}"[:900])
    heads = " | ".join(f"{d}: {v['headline']}" for d, v in digests.items())
    print(f"::notice title=Google Cloud day 51 headlines::{heads[:900]}")
    if s["wrong"]:
        print("::warning title=Google Cloud day 51::AI.AGG differs from the answer key on "
              + "; ".join(f"{d}: " + ", ".join(f"{f}={v}" for f, v in w.items()) for d, w in s["wrong"].items())[:700])
    return 0


def summarise() -> dict:
    cs, key = comments(), answer_key()
    return {"function": "AI.AGG", "new_argument": "output_schema", "comments": len(cs), "departments": len(key),
            "fields_per_department": len(FIELDS), "fields_checked": len(key) * len(FIELDS),
            "complaints": sum(c["issue"] != "none" for c in cs),
            "sold_out_mentions": sum(c["out_of_stock"] for c in cs), "queries": 1, "answer_key": key}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql())
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2]))
    else:
        s = summarise()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "hand-labelled answer key (local) / AI.AGG with output_schema on BigQuery (cloud)", **s},
            indent=2) + "\n")
        print(json.dumps(s, indent=2))
