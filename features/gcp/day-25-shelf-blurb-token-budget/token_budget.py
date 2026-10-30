"""Day 25 (Google Cloud): size a Gemini shelf-tag job before running it, with BigQuery's AI.COUNT_TOKENS (GA,
2026-10-27).

  python token_budget.py                # local: estimate tokens per prompt with a simple word-piece rule; writes
                                        # result.json
  python token_budget.py --sql          # print the BigQuery SQL (prompts inlined, no table needed)
  python token_budget.py --compare OUT  # check bq's JSON output against the local estimate (cloud.sh)

The job: write a shelf-tag blurb for each of 24 grocery products with Gemini. Two prompt templates do the same job, a
verbose one and a short one. AI.COUNT_TOKENS returns the number of tokens the model's tokenizer produces for each
prompt without making a billable generation call, so the input side of both versions can be compared, and the cheaper
one picked, before a single blurb is generated.

The local run can't reproduce Gemini's tokenizer, so the cloud check is on what must hold whatever the exact counts:
every prompt gets a positive count, the short template is smaller for every product, the saving is within 15 points
of the local estimate, and the cloud total is within 0.5x-2x of the estimate. The real counts are reported.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIECE = re.compile(r"[A-Za-z]+|\d|[^\sA-Za-z\d]")


def products() -> list[dict]:
    with open(HERE / "data" / "products.csv", newline="") as f:
        return list(csv.DictReader(f))


def config() -> dict:
    return json.loads((HERE / "data" / "prompts.json").read_text())


def prompts() -> list[dict]:
    """One row per (product, template)."""
    t = config()["templates"]
    return [{"barcode": p["barcode"], "variant": v, "prompt": t[v].format(**p)} for p in products() for v in sorted(t)]


def estimate(text: str) -> int:
    """Rough tokenizer stand-in: each digit and punctuation mark is a token, words cost one token per 4 letters."""
    return sum(math.ceil(len(m) / 4) if m[0].isalpha() else 1 for m in PIECE.findall(text))


def summarise(counts: dict[tuple[str, str], int]) -> dict:
    by_variant = {v: sum(n for (b, var), n in counts.items() if var == v) for v in ("short", "verbose")}
    per_product = {b for b, _ in counts}
    largest = max(per_product, key=lambda b: (counts[(b, "short")], b))
    names = {p["barcode"]: p["name"] for p in products()}
    return {
        "endpoint": config()["endpoint"],
        "prompts": len(counts),
        "products": len(per_product),
        "tokens_verbose": by_variant["verbose"],
        "tokens_short": by_variant["short"],
        "saving_pct": round(100 * (1 - by_variant["short"] / by_variant["verbose"]), 1),
        "short_smaller_for_every_product": all(counts[(b, "short")] < counts[(b, "verbose")] for b in per_product),
        "largest_short_prompt": {"product": names[largest], "tokens": counts[(largest, "short")]},
    }


def local_counts() -> dict[tuple[str, str], int]:
    return {(r["barcode"], r["variant"]): estimate(r["prompt"]) for r in prompts()}


def sql() -> str:
    endpoint = config()["endpoint"]
    rows = ",\n    ".join(
        f"STRUCT({json.dumps(r['barcode'])} AS barcode, {json.dumps(r['variant'])} AS variant, "
        f"{json.dumps(r['prompt'])} AS prompt)" for r in prompts())
    return f"""-- Day 25: input tokens of two shelf-tag prompt templates, counted by AI.COUNT_TOKENS (GA 2026-10-27).
-- No generation call is made. TO_JSON_STRING keeps the whole return value, so the reader handles either a plain
-- INT64 or a struct with a `result` field.
SELECT barcode, variant,
       TO_JSON_STRING(AI.COUNT_TOKENS(prompt, endpoint => '{endpoint}')) AS raw
FROM UNNEST([
    {rows}
])
ORDER BY barcode, variant
"""


def count_from_json(raw) -> int | None:
    v = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(v, dict):
        v = v.get("result", v.get("total_tokens", v.get("token_count")))
    try:
        return int(v) if v is not None else None
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
    cloud = {(r["barcode"], r["variant"]): count_from_json(r.get("raw")) for r in rows}
    local = local_counts()
    problems = []
    if set(cloud) != set(local):
        problems.append(f"{len(cloud)} rows back, expected {len(local)}")
    bad = [k for k, n in cloud.items() if not n or n <= 0]
    if bad:
        sample = next((r.get("raw") for r in rows if (r["barcode"], r["variant"]) == bad[0]), None)
        problems.append(f"{len(bad)} prompts without a positive count (first raw value: {str(sample)[:200]})")
    if problems:
        print("::error title=Google Cloud day 25 differs from local::" + "; ".join(problems))
        return 1
    c, l = summarise(cloud), summarise(local)
    ratio = c["tokens_verbose"] / l["tokens_verbose"]
    checks = {
        "short template smaller for every product": c["short_smaller_for_every_product"],
        f"saving within 15 points of the local estimate ({c['saving_pct']}% vs {l['saving_pct']}%)":
            abs(c["saving_pct"] - l["saving_pct"]) <= 15,
        f"cloud total within 0.5x-2x of the estimate ({ratio:.2f}x)": 0.5 <= ratio <= 2,
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        print("::error title=Google Cloud day 25 differs from local::" + "; ".join(failed))
        return 1
    print(f"::notice title=Google Cloud day 25::AI.COUNT_TOKENS ({c['endpoint']}) counted {c['prompts']} prompts "
          f"with no generation call: verbose template {c['tokens_verbose']:,} input tokens, short {c['tokens_short']:,} "
          f"({c['saving_pct']}% fewer; local estimate {l['saving_pct']}%), short smaller for all {c['products']} "
          f"products; Gemini's count was {ratio:.2f}x the local estimate. Largest short prompt: "
          f"{c['largest_short_prompt']['product']} ({c['largest_short_prompt']['tokens']} tokens)")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql())
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2]))
    else:
        s = summarise(local_counts())
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "word-piece estimate (local) / AI.COUNT_TOKENS (cloud)", **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
