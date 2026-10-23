"""Day 18 (Google Cloud): weekly-ad deal finder with reusable column aliases (GoogleSQL, GA 2026-10-15, opt-in).
The shelf-tag math is a chain: promo price -> price per kg / checkout price with tax / saving, and the deal filter
uses the last links. With reusable column aliases each step is written once and later steps (and WHERE) refer to it
by name; the same query without the feature has to repeat the promo-price expression six times.

  python shelf_tags.py --local                 # DuckDB runs both queries (it already resolves such aliases); result.json
  python shelf_tags.py --sql setup P.DS        # CREATE TABLE P.DS.shelf_items AS ...
  python shelf_tags.py --sql aliases P.DS      # the deal finder written with reusable column aliases
  python shelf_tags.py --sql expanded P.DS     # the same query with every expression repeated (control)
  python shelf_tags.py --compare DIR           # check the bq outputs cloud.sh saved in DIR

The cloud check (cloud.sh, GitHub Actions), errors when:
  * the alias query fails once the feature is on, or its rows differ from the expanded query's rows
  * either BigQuery result differs from the local DuckDB result
Items and prices are made up; the dataset is short-lived and deleted by cloud.sh.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# sku, product, aisle, shelf price (cents), ad discount (%), pack size (g), sales tax (basis points)
ITEMS = [
    ("0702", "whole milk 1gal", "dairy", 389, 10, 3900, 0),
    ("0703", "greek yogurt 32oz", "dairy", 549, 25, 907, 0),
    ("0705", "butter 1lb", "dairy", 479, 15, 454, 0),
    ("0711", "large eggs 12ct", "dairy", 429, 0, 680, 0),
    ("0715", "cheddar block 8oz", "dairy", 399, 30, 227, 0),
    ("4011", "bananas 3lb bag", "produce", 177, 0, 1361, 0),
    ("3283", "honeycrisp apples 3lb", "produce", 599, 33, 1361, 0),
    ("4225", "avocados 4ct", "produce", 499, 20, 800, 0),
    ("4062", "cucumbers 3ct", "produce", 249, 0, 900, 0),
    ("2201", "sourdough loaf", "bakery", 499, 20, 680, 0),
    ("2207", "bagels 6ct", "bakery", 399, 25, 570, 0),
    ("2210", "croissants 4ct", "bakery", 549, 0, 240, 0),
    ("0903", "oat milk 64oz", "dairy alt", 479, 25, 1890, 0),
    ("0905", "almond milk 64oz", "dairy alt", 429, 15, 1890, 0),
    ("5101", "spaghetti 1lb", "pantry", 179, 0, 454, 0),
    ("5104", "marinara 24oz", "pantry", 399, 30, 680, 0),
    ("5110", "basmati rice 2lb", "pantry", 549, 20, 907, 0),
    ("5121", "rolled oats 42oz", "pantry", 599, 25, 1190, 0),
    ("6001", "frozen peas 12oz", "frozen", 229, 10, 340, 0),
    ("6010", "vanilla ice cream 48oz", "frozen", 649, 40, 1360, 0),
    ("6015", "frozen pizza", "frozen", 799, 25, 760, 0),
    ("7001", "tortilla chips 13oz", "snacks", 449, 33, 369, 0),
    ("7010", "dark chocolate bar", "snacks", 349, 20, 100, 0),
    ("8001", "paper towels 6ct", "household", 1299, 25, 1400, 825),
    ("8010", "dish soap 19oz", "household", 399, 20, 560, 825),
]
MIN_SAVING_CENTS = 75       # the ad's deal threshold
MAX_CENTS_PER_KG = 1200     # and it must be good value per kg (store unit-price rule)


def sql_text() -> str:
    return (HERE / "shelf_tags.sql").read_text()


def blocks() -> dict[str, str]:
    out = {}
    for chunk in sql_text().split("-- name:")[1:]:
        lines = chunk.strip().splitlines()
        out[lines[0].strip()] = "\n".join(l for l in lines[1:] if not l.lstrip().startswith("--")).strip().rstrip(";")
    return out


def items_values() -> str:
    return ",\n  ".join(f"('{s}', '{p}', '{a}', {c}, {d}, {g}, {t})" for s, p, a, c, d, g, t in ITEMS)


def bigquery_sql(part: str, target: str = "DATASET") -> str:
    b = blocks()
    return (b[part].replace("{items}", items_values()).replace("{table}", f"`{target}.shelf_items`")
            .replace("{min_saving}", str(MIN_SAVING_CENTS)).replace("{max_per_kg}", str(MAX_CENTS_PER_KG)))


# ---------- reference model (plain Python, used by the tests)
def _div(a: int, b: int) -> int:
    return a // b                                   # all operands are positive: same as BigQuery DIV


def pure_python() -> list[tuple]:
    rows = []
    for s, p, a, c, d, g, t in ITEMS:
        promo = _div(c * (100 - d) + 50, 100)
        per_kg = _div(promo * 1000 + g // 2, g)
        checkout = _div(promo * (10000 + t) + 5000, 10000)
        saving = c - promo
        if saving >= MIN_SAVING_CENTS and per_kg <= MAX_CENTS_PER_KG:
            rows.append((s, p, a, c, promo, saving, per_kg, checkout))
    return sorted(rows, key=lambda r: (r[6], r[0]))


# ---------- local: DuckDB runs the same SQL text (it resolves aliases in SELECT and WHERE natively)
def run_local(part: str) -> list[tuple]:
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE MACRO div(a, b) AS a // b")
    con.execute("CREATE SCHEMA ds")
    con.execute(f"CREATE TABLE ds.shelf_items AS SELECT * FROM (VALUES {items_values()}) "
                "AS t(sku, product, aisle, shelf_cents, ad_pct, pack_grams, tax_bp)")   # setup minus OPTIONS
    return [tuple(int(v) if isinstance(v, int) else v for v in r)
            for r in con.execute(bigquery_sql(part, "ds").replace("`", "")).fetchall()]


# ---------- cloud outputs
def _redact(text: str) -> str:
    return text.replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")


def read_rows(path: Path) -> list[tuple]:
    """bq --format=json output (may start with a warning line) -> rows in COLUMNS order."""
    text = path.read_text()
    dec = json.JSONDecoder()
    for i in (k for k, ch in enumerate(text) if ch == "["):
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, list) and all(isinstance(r, dict) for r in obj):
            return [tuple(int(r[c]) if c in INT_COLUMNS else r[c] for c in COLUMNS) for r in obj]
    print(f"::error title=Unreadable BigQuery output::{path.name}: {_redact(text[:400])!r}")
    raise SystemExit(1)


COLUMNS = ["sku", "product", "aisle", "shelf_cents", "promo_cents", "saving_cents", "cents_per_kg", "checkout_cents"]
INT_COLUMNS = set(COLUMNS[3:])


def compare(aliases: list[tuple], expanded: list[tuple], local: list[tuple]) -> list[str]:
    errors = []
    for name, got in (("aliases", aliases), ("expanded", expanded)):
        if got != local:
            only_c, only_l = sorted(set(got) - set(local)), sorted(set(local) - set(got))
            errors.append(f"{name}: {len(got)} rows vs {len(local)} local; only cloud {only_c[:2]}, "
                          f"only local {only_l[:2]}")
    if aliases != expanded:
        errors.append("alias query and expanded query disagree")
    return errors


def promo_mentions(part: str) -> int:
    """How many times the promo-price expression is written out in a query."""
    return bigquery_sql(part).count("shelf_cents * (100 - ad_pct)")


def summarise(rows: list[tuple]) -> dict:
    return {
        "items": len(ITEMS), "deals": len(rows),
        "rule": f"saving >= {MIN_SAVING_CENTS}c and promo price <= {MAX_CENTS_PER_KG}c per kg",
        "promo_expression_written": {"with_aliases": promo_mentions("aliases"),
                                     "without": promo_mentions("expanded")},
        "query_chars": {"with_aliases": len(bigquery_sql("aliases")), "without": len(bigquery_sql("expanded"))},
        "deals_table": [dict(zip(COLUMNS, r)) for r in rows],
        "headline": f"{len(rows)} of {len(ITEMS)} ad items pass the deal rule; promo-price expression written "
                    f"{promo_mentions('aliases')}x with reusable aliases vs {promo_mentions('expanded')}x without",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(bigquery_sql(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "DATASET"))
    elif mode == "--compare":
        d = Path(sys.argv[2])
        aliases, expanded = read_rows(d / "aliases.json"), read_rows(d / "expanded.json")
        errors = compare(aliases, expanded, run_local("aliases"))
        if errors:
            print(f"::error title=Day 18 reusable aliases differ::{len(errors)} difference(s): "
                  f"{_redact(json.dumps(errors))[:850]}")
            raise SystemExit(1)
        s = summarise(aliases)
        top = [f"{r[1]} {r[6]}c/kg" for r in aliases[:3]]
        print(f"::notice title=Google Cloud day 18::Reusable column aliases query matched the expanded query and "
              f"local DuckDB: {s['headline']}; best value {json.dumps(top)}")
    else:
        rows = run_local("aliases")
        s = summarise(rows)
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB (local) / BigQuery GoogleSQL with reusable "
                                                      "column aliases (cloud)", **s}, indent=2) + "\n")
        print(json.dumps({k: v for k, v in s.items() if k != "deals_table"}, indent=2))
