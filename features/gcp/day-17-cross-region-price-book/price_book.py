"""Day 17 (Google Cloud): ship the weekly price book to a second region with a cross-region table copy (GA
2026-10-15). The price book is built in a US dataset; one `bq cp` copies it, the table and its schema
included, into a dataset in the EU multi-region, where the EU analytics team can query it in-region. Then the US
table gets a markdown, to show the copy is a point-in-time snapshot and not live replication.

  python price_book.py --local                # plain Python aggregates; writes result.json
  python price_book.py --sql setup P.DS       # CREATE TABLE ... AS SELECT (US dataset P.DS)
  python price_book.py --sql edit P.DS        # the markdown applied in the US after the copy
  python price_book.py --sql check P.DS.T     # per-category counts, sums and a row fingerprint for table P.DS.T
  python price_book.py --compare DIR          # check the bq outputs cloud.sh saved in DIR

The cloud check (cloud.sh, GitHub Actions), errors when:
  * the EU copy isn't in the EU or its row count differs (whether clustering and description came along is
    reported in the notice, not enforced)
  * the EU copy's counts / sums differ from the local price book, or its fingerprint differs from the US table's
    fingerprint taken right after the copy
  * after the US markdown, the US table doesn't match the local edited book, or the EU copy changed with it
SKUs and prices are made up; both datasets are short-lived and deleted by cloud.sh.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATEGORIES = [("dairy", 349), ("produce", 129), ("bakery", 399), ("pantry", 279), ("frozen", 549), ("snacks", 329)]
N_SKUS = 36
DESCRIPTION = "Weekly shelf price book (made-up SKUs)"
MARKDOWN = {"sku": "S05", "cents_off": 30}   # applied in the US after the copy


def price_book() -> list[dict]:
    rows, seed = [], 20261021
    for i in range(1, N_SKUS + 1):
        seed = (seed * 1103515245 + 12345) % 2**31
        cat, base = CATEGORIES[(i - 1) % len(CATEGORIES)]
        rows.append({"sku": f"S{i:02d}", "category": cat, "price_cents": base + seed % 300})
    return rows


def edited_book() -> list[dict]:
    return [{**r, "price_cents": r["price_cents"] - MARKDOWN["cents_off"]} if r["sku"] == MARKDOWN["sku"] else r
            for r in price_book()]


def aggregates(rows: list[dict]) -> dict[str, tuple[int, int]]:
    """{category or 'ALL': (skus, total cents)}: what the check query returns, minus the fingerprint."""
    out = {"ALL": (len(rows), sum(r["price_cents"] for r in rows))}
    for c, _ in CATEGORIES:
        sel = [r["price_cents"] for r in rows if r["category"] == c]
        out[c] = (len(sel), sum(sel))
    return out


def bigquery_sql(part: str, target: str = "DATASET") -> str:
    if part == "setup":
        return (f"CREATE TABLE `{target}.price_book`\nCLUSTER BY category\n"
                f"OPTIONS (description = '{DESCRIPTION}')\nAS\n"
                f"SELECT sku, category, price_cents FROM UNNEST([\n"
                + ",\n".join(f"  STRUCT('{r['sku']}' AS sku, '{r['category']}' AS category, "
                             f"{r['price_cents']} AS price_cents)" for r in price_book()) + "\n])")
    if part == "edit":
        return (f"UPDATE `{target}.price_book` SET price_cents = price_cents - {MARKDOWN['cents_off']} "
                f"WHERE sku = '{MARKDOWN['sku']}'")
    if part == "check":
        fp = "BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(t)))"
        return (f"SELECT 'ALL' AS category, COUNT(*) AS skus, SUM(price_cents) AS total_cents, {fp} AS fp\n"
                f"FROM `{target}` AS t\nUNION ALL\n"
                f"SELECT category, COUNT(*), SUM(price_cents), {fp}\nFROM `{target}` AS t\nGROUP BY category\n"
                "ORDER BY category")
    raise SystemExit(f"unknown part {part}")


# ---------- reading bq output
def _redact(text: str) -> str:
    return text.replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")


def read_json(path: Path, want) -> object:
    """The last JSON value in a bq output file that satisfies want(); bq may print a warning line first."""
    text = path.read_text()
    dec, found = json.JSONDecoder(), None
    starts = [k for k, ch in enumerate(text) if ch in "[{"]
    for i in starts:
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            continue
        if want(obj):
            found = obj
            break
    if found is None:
        print(f"::error title=Unreadable BigQuery output::{path.name}: {_redact(text[:400])!r}")
        raise SystemExit(1)
    return found


def read_check(path: Path) -> dict[str, tuple[int, int, str]]:
    rows = read_json(path, lambda o: isinstance(o, list) and o and isinstance(o[0], dict) and "fp" in o[0])
    return {r["category"]: (int(r["skus"]), int(r["total_cents"]), str(r["fp"])) for r in rows}


def read_meta(path: Path) -> dict:
    t = read_json(path, lambda o: isinstance(o, dict) and "tableReference" in o)
    return {"location": t.get("location"), "clustering": (t.get("clustering") or {}).get("fields"),
            "description": t.get("description"), "num_rows": int(t.get("numRows", -1))}


# ---------- comparison
def _vs_local(name: str, got: dict[str, tuple[int, int, str]], local: dict[str, tuple[int, int]]) -> list[str]:
    errors = []
    for k in sorted(set(got) | set(local)):
        g = got.get(k, (None, None, None))[:2]
        if g != local.get(k):
            errors.append(f"{name} {k}: {g} vs local {local.get(k)}")
    return errors


def compare(us_at_copy: dict, eu: dict, us_after: dict, eu_after: dict, meta: dict) -> list[str]:
    before, after = aggregates(price_book()), aggregates(edited_book())
    errors = []
    if meta["location"] != "EU":
        errors.append(f"EU copy location is {meta['location']}")
    if meta["num_rows"] != N_SKUS:
        errors.append(f"EU copy has {meta['num_rows']} rows")
    errors += _vs_local("US at copy", us_at_copy, before)
    errors += _vs_local("EU copy", eu, before)
    errors += _vs_local("US after markdown", us_after, after)
    errors += _vs_local("EU after US markdown", eu_after, before)
    errors += [f"fingerprint {k}: EU copy differs from US at copy" for k in sorted(us_at_copy)
               if eu.get(k, (0, 0, None))[2] != us_at_copy[k][2]]
    errors += [f"fingerprint {k}: EU copy changed after the US markdown" for k in sorted(eu)
               if eu_after.get(k, (0, 0, None))[2] != eu[k][2]]
    cat = next(r["category"] for r in price_book() if r["sku"] == MARKDOWN["sku"])
    if us_after.get(cat, (0, 0, None))[2] == us_at_copy.get(cat, (0, 0, None))[2]:
        errors.append(f"US {cat} fingerprint didn't change after the markdown")
    return errors


def summarise() -> dict:
    book, before, after = price_book(), aggregates(price_book()), aggregates(edited_book())
    cat = next(r["category"] for r in book if r["sku"] == MARKDOWN["sku"])
    return {
        "skus": N_SKUS, "categories": len(CATEGORIES), "source_location": "US", "copy_location": "EU",
        "copied_rows": before["ALL"][0], "price_book_total_cents": before["ALL"][1],
        "markdown_after_copy": {**MARKDOWN, "category": cat},
        "us_total_after_markdown_cents": after["ALL"][1], "eu_total_after_markdown_cents": before["ALL"][1],
        "by_category": [{"category": c, "skus": before[c][0], "total_cents": before[c][1]} for c, _ in CATEGORIES],
        "headline": f"{N_SKUS} SKUs copied US -> EU in one bq cp; "
                    f"after a {MARKDOWN['cents_off']}c markdown on {MARKDOWN['sku']} in the US, the EU copy still "
                    f"holds the copied snapshot (US {after['ALL'][1]}c vs EU {before['ALL'][1]}c)",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(bigquery_sql(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "DATASET"))
    elif mode == "--compare":
        d = Path(sys.argv[2])
        parts = {n: read_check(d / f"{n}.json") for n in ("us_at_copy", "eu", "us_after", "eu_after")}
        errors = compare(**parts, meta=read_meta(d / "eu_meta.json"))
        if errors:
            print(f"::error title=Day 17 cross-region copy differs::{len(errors)} difference(s): "
                  f"{_redact(json.dumps(errors))[:850]}")
            raise SystemExit(1)
        s = summarise()
        meta = read_meta(d / "eu_meta.json")
        kept = {"clustering": meta["clustering"], "description_kept": meta["description"] == DESCRIPTION}
        print(f"::notice title=Google Cloud day 17::Cross-region table copy US -> EU matched local and the US "
              f"fingerprint: {s['headline']}; EU copy metadata {json.dumps(kept)}")
    else:
        s = summarise()
        (HERE / "result.json").write_text(json.dumps({"engine": "plain Python (local) / BigQuery cross-region "
                                                      "table copy, bq cp US -> EU (cloud)", **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
