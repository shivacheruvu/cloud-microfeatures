"""Day 50 (Google Cloud): unit-price shelf labels from messy pack sizes with BigQuery's AI.GENERATE on the
gemini-4-neon model (GA in BigQuery generative AI functions, 2026-11-19), next to the default model.

  python unit_prices.py                # local: the hand-worked answer key and unit prices; writes result.json
  python unit_prices.py --sql          # print the BigQuery SQL (products inlined, no table needed)
  python unit_prices.py --compare OUT  # check bq's JSON output against the answer key (cloud.sh)

The job: 14 Thanksgiving-shop products whose size lives only in the name ("Sparkling water 12 x 355 ml cans",
"Large brown eggs, one dozen", "Stuffing mix six-pack, 170 g boxes", "Heavy cream 1 pint (473 ml)"). A unit-price
label (price per 100 g, per 100 ml or per item) needs the pack count, the size of one unit and its unit. One query asks
gemini-4-neon and the default model (gemini-3.5-flash-lite) for those three typed fields; Python turns them into labels.

Must hold (else the run fails): 14 rows back; every gemini-4-neon answer has an empty status, a positive whole
pack_count, a positive amount and a unit from the allowed list. Reported for both models: fields equal to the answer key
(42 = 14 x 3), products fully right and unit-price labels right to the cent. A gemini-4-neon difference is a warning
(a finding about the model, not a broken run).
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEW_ENDPOINT = "gemini-4-neon"
BASELINE_ENDPOINT = "gemini-3.5-flash-lite"  # AI.GENERATE's documented default
MODELS = {"neon": NEW_ENDPOINT, "baseline": BASELINE_ENDPOINT}
FIELDS = ["pack_count", "amount", "unit"]
# unit -> (label family, factor to the base unit: grams, millilitres or items)
UNITS = {"g": ("100 g", 1.0), "kg": ("100 g", 1000.0), "oz": ("100 g", 28.349523125), "lb": ("100 g", 453.59237),
         "ml": ("100 ml", 1.0), "l": ("100 ml", 1000.0), "fl_oz": ("100 ml", 29.5735295625),
         "gal": ("100 ml", 3785.411784), "each": ("item", 1.0)}
INSTRUCTION = (
    "Read the pack size from a grocery product name. pack_count is how many identical sealed units are sold together "
    "(1 for a single item; 12 for 12 x 355 ml cans; six-pack is 6). amount and unit are the size of ONE of those "
    "units, exactly as stated, not converted. unit must be one of: g, kg, oz, lb, ml, l, fl_oz, gal, each. Use fl_oz "
    "for fluid ounces and oz for weight ounces. If a metric size is given in brackets, use the metric size. Items sold "
    "by count with no weight or volume (rolls, eggs) are pack_count 1, amount = number of pieces, unit each; a dozen "
    "is 12. Product name: ")
OUTPUT_SCHEMA = ("pack_count INT64 OPTIONS(description = \"number of identical units sold together\"), "
                 "amount FLOAT64 OPTIONS(description = \"size of one unit as stated\"), "
                 "unit STRING OPTIONS(description = \"one of g, kg, oz, lb, ml, l, fl_oz, gal, each\")")


def products() -> list[dict]:
    with open(HERE / "data" / "products.csv", newline="") as f:
        return [{**r, "product_no": int(r["product_no"]), "price_cents": int(r["price_cents"]),
                 "pack_count": int(r["pack_count"]), "amount": float(r["amount"])} for r in csv.DictReader(f)]


def label(price_cents: int, pack_count: int, amount: float, unit: str) -> str:
    """The shelf label, e.g. '$1.41 / 100 ml' or '$0.38 / item'."""
    family, factor = UNITS[unit]
    base = pack_count * amount * factor
    per = price_cents / 100 / base * (100 if family != "item" else 1)
    return f"${per:.2f} / {family}"


def _lit(s: str) -> str:
    """A GoogleSQL string literal (JSON escaping is valid GoogleSQL for these characters)."""
    return json.dumps(s, ensure_ascii=False)


def sql() -> str:
    rows = ",\n    ".join(f"STRUCT({r['product_no']} AS product_no, {_lit(r['product'])} AS product)"
                          for r in products())
    picks = ",\n       ".join(f"TO_JSON_STRING(STRUCT({a}.pack_count, {a}.amount, {a}.unit, {a}.status)) AS {a}"
                              for a in MODELS)
    gens = ",\n         ".join(
        f"AI.GENERATE(CONCAT({_lit(INSTRUCTION)}, product), endpoint => '{e}', "
        f"output_schema => '{OUTPUT_SCHEMA}') AS {a}" for a, e in MODELS.items())
    return f"""-- Day 50: pack sizes for unit-price labels with AI.GENERATE on {NEW_ENDPOINT}
-- (GA in BigQuery generative AI functions 2026-11-19) next to {BASELINE_ENDPOINT}; output_schema gives typed fields.
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


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def clean(a: dict | None) -> dict | None:
    """A usable answer (count, amount, unit) or None."""
    if not a or a.get("status"):
        return None
    n, amt = _num(a.get("pack_count")), _num(a.get("amount"))
    unit = str(a.get("unit") or "").strip().lower().replace(" ", "_").replace("fl._oz", "fl_oz")
    if n is None or n != int(n) or amt is None or unit not in UNITS:
        return None
    return {"pack_count": int(n), "amount": amt, "unit": unit}


def check(rows: list[dict]) -> tuple[list[str], dict[str, dict[int, dict | None]]]:
    """Problems that make the run fail, and the parsed answers per model per product."""
    key = {r["product_no"]: r for r in products()}
    problems, got = [], {}
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
        if n not in got:
            continue
        raw = parsed["neon"][n]
        if raw is None:
            problems.append(f"product {n}: no {NEW_ENDPOINT} answer")
        elif raw.get("status"):
            problems.append(f"product {n}: {NEW_ENDPOINT} status {str(raw['status'])[:160]}")
        elif clean(raw) is None:
            problems.append(f"product {n}: {NEW_ENDPOINT} gave an unusable size {json.dumps(raw)[:120]}")
    return problems, parsed


def score(parsed: dict[int, dict | None]) -> dict:
    key = {r["product_no"]: r for r in products()}
    fields_right, wrong_products, wrong_labels = 0, [], []
    for n, k in key.items():
        a = clean(parsed.get(n))
        ok = 0
        if a:
            ok += a["pack_count"] == k["pack_count"]
            ok += abs(a["amount"] - k["amount"]) < 1e-6
            ok += a["unit"] == k["unit"]
        fields_right += ok
        if ok != len(FIELDS):
            wrong_products.append(n)
        want = label(k["price_cents"], k["pack_count"], k["amount"], k["unit"])
        if not a or label(k["price_cents"], a["pack_count"], a["amount"], a["unit"]) != want:
            wrong_labels.append(n)
    return {"fields_right": fields_right, "fields": len(key) * len(FIELDS),
            "products_right": len(key) - len(wrong_products), "labels_right": len(key) - len(wrong_labels),
            "products": len(key), "wrong_products": wrong_products, "wrong_labels": wrong_labels}


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
        print("::error title=Google Cloud day 50 output check failed::" + "; ".join(problems)[:900])
        return 1
    c, b = score(parsed["neon"]), score(parsed["baseline"])

    def line(name, s):
        return (f"{name} {s['fields_right']}/{s['fields']} fields, {s['products_right']}/{s['products']} products "
                f"fully right, {s['labels_right']}/{s['products']} unit-price labels right"
                + (f" (wrong: {', '.join(map(str, s['wrong_products']))})" if s["wrong_products"] else ""))
    print(f"::notice title=Google Cloud day 50::AI.GENERATE with output_schema (pack_count, amount, unit) read the "
          f"pack size of {c['products']} product names in one query. "
          + (line(NEW_ENDPOINT, c) + "; " + line(BASELINE_ENDPOINT, b))[:800])
    if c["wrong_products"]:
        bad = {n: clean(parsed["neon"][n]) for n in c["wrong_products"]}
        print(f"::warning title=Google Cloud day 50::{NEW_ENDPOINT} differs from the answer key on "
              + "; ".join(f"{n}: {v['pack_count']} x {v['amount']:g} {v['unit']}" for n, v in bad.items())[:700])
    return 0


def summarise() -> dict:
    rows = products()
    labels = {str(r["product_no"]): {"product": r["product"],
                                     "size": f"{r['pack_count']} x {r['amount']:g} {r['unit']}",
                                     "label": label(r["price_cents"], r["pack_count"], r["amount"], r["unit"])}
              for r in rows}
    return {"new_endpoint": NEW_ENDPOINT, "baseline_endpoint": BASELINE_ENDPOINT, "products": len(rows),
            "fields_per_product": len(FIELDS), "fields_checked": len(rows) * len(FIELDS),
            "multipacks": sum(r["pack_count"] > 1 for r in rows),
            "label_families": {f: sum(UNITS[r["unit"]][0] == f for r in rows) for f in ("100 g", "100 ml", "item")},
            "queries": 1, "model_calls": 2 * len(rows), "answer_key": labels}


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
        for n, v in s["answer_key"].items():
            print(n, v["product"], "|", v["size"], "|", v["label"])
