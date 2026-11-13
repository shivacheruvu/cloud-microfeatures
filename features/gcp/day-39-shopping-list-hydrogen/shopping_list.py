"""Day 39 (Google Cloud): turn messy shopping-list lines into a quantity in a base unit with BigQuery's AI.GENERATE on
the gemini-4-hydrogen model (GA in BigQuery generative AI functions, 2026-11-12), next to the default model.

  python shopping_list.py                # local: the hand-worked answer key; writes result.json
  python shopping_list.py --sql          # print the BigQuery SQL (lines inlined, no table needed)
  python shopping_list.py --compare OUT  # check bq's JSON output against the answer key (cloud.sh)

The job: 14 lines a customer typed into a click-and-collect order ("2 dozen eggs", "half a kilo of ground beef",
"three 2L bottles of milk", "1.5 lb chicken thighs", "a dozen and a half lemons"). The picker needs one number in one
base unit per line: each, g or ml. A regex can't read "a baker's dozen" or "two 400g tins"; a model can, and
output_schema makes the answer typed columns (quantity FLOAT64, unit STRING) rather than text to parse.

One query asks gemini-4-hydrogen and the default model (gemini-3.5-flash-lite) the same question for each line.
Must hold (else the run fails): 14 rows back; every gemini-4-hydrogen answer has an empty status, a quantity and a
unit from {each, g, ml}. Reported: lines where the quantity is within 1 % of the answer key and the unit matches, for
both models; a gemini-4-hydrogen miss is a warning (a finding about the model, not a broken run).
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEW_ENDPOINT = "gemini-4-hydrogen"
BASELINE_ENDPOINT = "gemini-3.5-flash-lite"  # AI.GENERATE's documented default
MODELS = {"hydrogen": NEW_ENDPOINT, "baseline": BASELINE_ENDPOINT}
UNITS = ["each", "g", "ml"]
TOLERANCE = 0.01
INSTRUCTION = (
    "A customer typed this line into a grocery click-and-collect order. Say how much the picker must pick in total, "
    "as one number in one base unit: unit must be exactly one of each, g, ml (convert kg, lb and oz to g, and L to "
    "ml; 1 lb = 453.592 g; count items, dozens and packs as each unless a weight or volume is given). Multiply "
    "pack counts out, e.g. two 500g bags is 1000 g. item is the product name in lower case. Line: ")
OUTPUT_SCHEMA = (
    'item STRING OPTIONS(description = "product name, lower case"), '
    'quantity FLOAT64 OPTIONS(description = "total amount to pick, in unit"), '
    'unit STRING OPTIONS(description = "one of: each, g, ml")')


def lines() -> list[dict]:
    with open(HERE / "data" / "lines.csv", newline="") as f:
        return [{**r, "line_no": int(r["line_no"]), "quantity": float(r["quantity"])} for r in csv.DictReader(f)]


def _lit(s: str) -> str:
    """A GoogleSQL string literal (JSON escaping is valid GoogleSQL for these characters)."""
    return json.dumps(s, ensure_ascii=False)


def _gen(alias: str, endpoint: str) -> str:
    return (f"AI.GENERATE(CONCAT({_lit(INSTRUCTION)}, line), endpoint => '{endpoint}', "
            f"output_schema => '{OUTPUT_SCHEMA}') AS {alias}")


def sql() -> str:
    rows = ",\n    ".join(f"STRUCT({r['line_no']} AS line_no, {_lit(r['line'])} AS line)" for r in lines())
    picks = ",\n       ".join(f"TO_JSON_STRING(STRUCT({a}.item, {a}.quantity, {a}.unit, {a}.status)) AS {a}"
                              for a in MODELS)
    gens = ",\n         ".join(_gen(a, e) for a, e in MODELS.items())
    return f"""-- Day 39: shopping-list lines to a quantity in a base unit with AI.GENERATE on {NEW_ENDPOINT}
-- (GA in BigQuery generative AI functions 2026-11-12) next to {BASELINE_ENDPOINT}; output_schema types the answer.
SELECT line_no,
       {picks}
FROM (
  SELECT line_no,
         {gens}
  FROM UNNEST([
    {rows}
  ])
)
ORDER BY line_no
"""


def _parse(raw) -> dict | None:
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def correct(answer: dict | None, key: dict) -> bool:
    if not answer or answer.get("status") or answer.get("quantity") is None:
        return False
    try:
        q = float(answer["quantity"])
    except (TypeError, ValueError):
        return False
    unit = str(answer.get("unit") or "").strip().lower()
    return unit == key["unit"] and abs(q - key["quantity"]) <= TOLERANCE * key["quantity"]


def check(rows: list[dict]) -> tuple[list[str], dict[str, dict[int, dict | None]]]:
    """Problems that make the run fail, and the parsed answers per model per line."""
    key = {r["line_no"]: r for r in lines()}
    problems = []
    got = {}
    for r in rows:
        try:
            got[int(r.get("line_no"))] = r
        except (TypeError, ValueError):
            problems.append(f"bad line_no {r.get('line_no')!r}")
    if sorted(got) != sorted(key):
        problems.append(f"lines back {sorted(got)}, expected 1..{len(key)}")
    parsed: dict[str, dict[int, dict | None]] = {a: {} for a in MODELS}
    for n in key:
        for a in MODELS:
            parsed[a][n] = _parse((got.get(n) or {}).get(a))
        h = parsed["hydrogen"][n]
        if n not in got:
            continue
        if h is None:
            problems.append(f"line {n}: no {NEW_ENDPOINT} answer")
        elif h.get("status"):
            problems.append(f"line {n}: {NEW_ENDPOINT} status {str(h['status'])[:160]}")
        elif h.get("quantity") is None or str(h.get("unit") or "").strip().lower() not in UNITS:
            problems.append(f"line {n}: {NEW_ENDPOINT} gave quantity {h.get('quantity')!r} unit {h.get('unit')!r}")
    return problems, parsed


def score(parsed: dict[int, dict | None]) -> tuple[int, list[int]]:
    key = {r["line_no"]: r for r in lines()}
    misses = [n for n in key if not correct(parsed.get(n), key[n])]
    return len(key) - len(misses), misses


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
        print("::error title=Google Cloud day 39 output check failed::" + "; ".join(problems)[:900])
        return 1
    key = {r["line_no"]: r for r in lines()}
    h_ok, h_miss = score(parsed["hydrogen"])
    b_ok, b_miss = score(parsed["baseline"])
    b_failed = [n for n, v in parsed["baseline"].items() if not v or v.get("status")]

    def show(a, n):
        v = parsed[a][n] or {}
        return f"{v.get('quantity')} {v.get('unit')}"
    print(f"::notice title=Google Cloud day 39::AI.GENERATE with output_schema on {NEW_ENDPOINT} answered all "
          f"{len(key)} shopping-list lines in one query: {h_ok}/{len(key)} equal the answer key (quantity within 1 %, "
          f"same base unit); {BASELINE_ENDPOINT} {b_ok}/{len(key)}"
          + (f" ({len(b_failed)} with an error status)" if b_failed else "") + ". "
          + "; ".join(f"line {n} '{key[n]['line']}': key {key[n]['quantity']:g} {key[n]['unit']}, hydrogen "
                      f"{show('hydrogen', n)}, baseline {show('baseline', n)}"
                      for n in sorted(set(h_miss) | set(b_miss)))[:700])
    if h_miss:
        print(f"::warning title=Google Cloud day 39::{NEW_ENDPOINT} differs from the answer key on line(s) "
              + ", ".join(str(n) for n in h_miss))
    return 0


def summarise() -> dict:
    rows = lines()
    by_unit = {u: sum(1 for r in rows if r["unit"] == u) for u in UNITS}
    return {"new_endpoint": NEW_ENDPOINT, "baseline_endpoint": BASELINE_ENDPOINT, "lines": len(rows),
            "lines_by_unit": by_unit, "tolerance": TOLERANCE, "queries": 1, "model_calls": 2 * len(rows),
            "answer_key": {str(r["line_no"]): {"line": r["line"], "quantity": r["quantity"], "unit": r["unit"]}
                           for r in rows}}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql())
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2]))
    else:
        s = summarise()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": f"hand-worked answer key (local) / AI.GENERATE on {NEW_ENDPOINT} vs {BASELINE_ENDPOINT} "
                       "(cloud)", **s}, indent=2) + "\n")
        print(json.dumps({k: v for k, v in s.items() if k != "answer_key"}, indent=2))
