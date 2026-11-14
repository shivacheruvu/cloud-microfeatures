"""Day 40 (Google Cloud): flag allergens in own-brand ingredient lists with BigQuery's AI.GENERATE on the
gemini-4-carbon model (GA in BigQuery generative AI functions, 2026-11-10), next to the default model.

  python allergens.py                # local: the hand-worked answer key; writes result.json
  python allergens.py --sql          # print the BigQuery SQL (products inlined, no table needed)
  python allergens.py --compare OUT  # check bq's JSON output against the answer key (cloud.sh)

The job: 14 deli and own-brand products with their ingredient lists ("whey protein isolate, cashew butter, cocoa, soy
lecithin"; "durum wheat semolina, eggs"). The shelf label needs six yes/no flags per product: milk, egg, peanut, tree
nut, wheat, soy. A keyword list misses "whey", "semolina", "parmesan" and "lecithin" or flags "coconut milk" as milk;
a model reads them, and output_schema returns six BOOL columns rather than text to parse.

One query asks gemini-4-carbon and the default model (gemini-3.5-flash-lite) the same question for each product.
Must hold (else the run fails): 14 rows back; every gemini-4-carbon answer has an empty status and all six flags as
booleans. Reported: flags equal to the answer key (84 = 14 x 6) and products with every flag right, for both models;
missed allergens (key yes, model no) are called out because those are the dangerous ones. A gemini-4-carbon
difference is a warning (a finding about the model, not a broken run).
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEW_ENDPOINT = "gemini-4-carbon"
BASELINE_ENDPOINT = "gemini-3.5-flash-lite"  # AI.GENERATE's documented default
MODELS = {"carbon": NEW_ENDPOINT, "baseline": BASELINE_ENDPOINT}
FLAGS = ["milk", "egg", "peanut", "tree_nut", "wheat", "soy"]
INSTRUCTION = (
    "You label a grocery product for allergens. From the ingredient list, say for each allergen whether the product "
    "contains it (true) or not (false). milk includes whey, casein, butter, cream and cheese such as parmesan; "
    "coconut milk is not milk. egg includes yolks and egg whites. peanut is peanuts and peanut butter. tree_nut is "
    "almonds, cashews, pecans, walnuts, hazelnuts, pine nuts and similar; coconut is not a tree nut and peanuts are "
    "not tree nuts. wheat includes durum, semolina and wheat flour, also inside soy sauce when listed. soy includes "
    "soybean oil, soy lecithin and soy sauce. Ignore sesame, fish and other allergens. Ingredients: ")
OUTPUT_SCHEMA = ", ".join(f'{f} BOOL OPTIONS(description = "true if the product contains {f.replace("_", " ")}")'
                          for f in FLAGS)


def products() -> list[dict]:
    with open(HERE / "data" / "products.csv", newline="") as f:
        return [{**r, "product_no": int(r["product_no"]), **{k: r[k] == "1" for k in FLAGS}}
                for r in csv.DictReader(f)]


def _lit(s: str) -> str:
    """A GoogleSQL string literal (JSON escaping is valid GoogleSQL for these characters)."""
    return json.dumps(s, ensure_ascii=False)


def sql() -> str:
    rows = ",\n    ".join(f"STRUCT({r['product_no']} AS product_no, {_lit(r['ingredients'])} AS ingredients)"
                          for r in products())
    flags = ", ".join(f"{{a}}.{f}" for f in FLAGS)
    picks = ",\n       ".join(f"TO_JSON_STRING(STRUCT({flags.format(a=a)}, {a}.status)) AS {a}" for a in MODELS)
    gens = ",\n         ".join(
        f"AI.GENERATE(CONCAT({_lit(INSTRUCTION)}, ingredients), endpoint => '{e}', "
        f"output_schema => '{OUTPUT_SCHEMA}') AS {a}" for a, e in MODELS.items())
    return f"""-- Day 40: allergen flags from ingredient lists with AI.GENERATE on {NEW_ENDPOINT}
-- (GA in BigQuery generative AI functions 2026-11-10) next to {BASELINE_ENDPOINT}; output_schema gives six BOOLs.
SELECT product_no,
       {picks}
FROM (
  SELECT product_no,
         {gens}
  FROM UNNEST([
    {rows}
  ])
)
ORDER BY product_no
"""


def _parse(raw) -> dict | None:
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return None


def check(rows: list[dict]) -> tuple[list[str], dict[str, dict[int, dict | None]]]:
    """Problems that make the run fail, and the parsed answers per model per product."""
    key = {r["product_no"]: r for r in products()}
    problems = []
    got = {}
    for r in rows:
        try:
            got[int(r.get("product_no"))] = r
        except (TypeError, ValueError):
            problems.append(f"bad product_no {r.get('product_no')!r}")
    if sorted(got) != sorted(key):
        problems.append(f"products back {sorted(got)}, expected 1..{len(key)}")
    parsed: dict[str, dict[int, dict | None]] = {a: {} for a in MODELS}
    for n in key:
        for a in MODELS:
            parsed[a][n] = _parse((got.get(n) or {}).get(a))
        c = parsed["carbon"][n]
        if n not in got:
            continue
        if c is None:
            problems.append(f"product {n}: no {NEW_ENDPOINT} answer")
        elif c.get("status"):
            problems.append(f"product {n}: {NEW_ENDPOINT} status {str(c['status'])[:160]}")
        else:
            bad = [f for f in FLAGS if _bool(c.get(f)) is None]
            if bad:
                problems.append(f"product {n}: {NEW_ENDPOINT} gave no boolean for {', '.join(bad)}")
    return problems, parsed


def score(parsed: dict[int, dict | None]) -> dict:
    """Flags right, products fully right, missed allergens (key true, answer not true), false alarms."""
    key = {r["product_no"]: r for r in products()}
    right, missed, extra, wrong_products = 0, [], [], []
    for n, k in key.items():
        a = parsed.get(n) or {}
        ok = True
        if a.get("status"):
            a = {}
        for f in FLAGS:
            v = _bool(a.get(f))
            if v == k[f]:
                right += 1
                continue
            ok = False
            (missed if k[f] else extra).append(f"{n}:{f}")
        if not ok:
            wrong_products.append(n)
    return {"flags_right": right, "flags": len(key) * len(FLAGS), "products_right": len(key) - len(wrong_products),
            "products": len(key), "missed": missed, "false_alarms": extra, "wrong_products": wrong_products}


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
        print("::error title=Google Cloud day 40 output check failed::" + "; ".join(problems)[:900])
        return 1
    c, b = score(parsed["carbon"]), score(parsed["baseline"])

    def line(name, s):
        return (f"{name} {s['flags_right']}/{s['flags']} flags, {s['products_right']}/{s['products']} products fully "
                f"right, missed allergens: {', '.join(s['missed']) or 'none'}, false alarms: "
                f"{', '.join(s['false_alarms']) or 'none'}")
    print(f"::notice title=Google Cloud day 40::AI.GENERATE with output_schema (six BOOLs) flagged allergens in "
          f"{c['products']} ingredient lists in one query. " + (line(NEW_ENDPOINT, c) + "; " + line(BASELINE_ENDPOINT, b))[:800])
    if c["wrong_products"]:
        print(f"::warning title=Google Cloud day 40::{NEW_ENDPOINT} differs from the answer key on "
              + ", ".join(c["missed"] + c["false_alarms"]))
    return 0


def summarise() -> dict:
    rows = products()
    return {"new_endpoint": NEW_ENDPOINT, "baseline_endpoint": BASELINE_ENDPOINT, "products": len(rows),
            "flags_per_product": len(FLAGS), "flags_checked": len(rows) * len(FLAGS),
            "products_with": {f: sum(r[f] for r in rows) for f in FLAGS},
            "allergen_free_products": sum(not any(r[f] for f in FLAGS) for r in rows),
            "queries": 1, "model_calls": 2 * len(rows),
            "answer_key": {str(r["product_no"]): {"product": r["product"], "contains": [f for f in FLAGS if r[f]]}
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
