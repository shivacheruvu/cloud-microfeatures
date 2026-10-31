"""Day 19 (Google Cloud): shelf unit prices from free-text pack sizes with a BigQuery Python UDF (GA 2026-05-20).
Grocery labels write sizes every way ("1 gal", "64 fl oz", "2 x 1 l", "12pk 12 fl oz", "1.5 lb", "12ct"); a regex
in SQL gets ugly fast, so parse_pack.py (plain Python, standard library) turns a label into a base quantity in g, ml
or count, and unit_prices.sql ranks each category by price per 100 g / 100 ml / item.

  python pack_prices.py --local               # DuckDB runs unit_prices.sql with parse_pack registered; result.json
  python pack_prices.py --sql udf P.DS        # CREATE FUNCTION P.DS.parse_pack ... LANGUAGE python (body = parse_pack.py)
  python pack_prices.py --sql setup P.DS      # CREATE TABLE P.DS.pack_items AS ...
  python pack_prices.py --sql query P.DS      # unit_prices.sql calling the Python UDF
  python pack_prices.py --compare FILE        # check the bq output cloud.sh saved

The cloud check (cloud.sh, GitHub Actions), errors when:
  * the Python UDF can't be created or the query calling it fails
  * BigQuery's rows differ from the local model (plain Python running the same parse_pack code; the test checks the
    DuckDB run of unit_prices.sql gives the same rows)
Items and prices are made up; the dataset (table and function) is short-lived and deleted by cloud.sh.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("parse_pack_day19", HERE / "parse_pack.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
parse_pack = _mod.parse_pack

# category, sku, shelf label, price (cents)
ITEMS = [
    ("milk", "0702", "whole milk 1 gal", 389),
    ("milk", "0706", "whole milk 64 fl oz", 259),
    ("milk", "0708", "organic whole milk 2 x 1 l", 549),
    ("yogurt", "0703", "greek yogurt 32oz", 549),
    ("yogurt", "0704", "greek yogurt 4 x 5.3 oz", 499),
    ("yogurt", "0709", "skyr 500g", 429),
    ("water", "1101", "sparkling water 12pk 12 fl oz", 599),
    ("water", "1102", "sparkling water 6 x 500ml", 449),
    ("water", "1103", "sparkling water 1.5L", 129),
    ("rice", "5110", "basmati rice 2lb", 549),
    ("rice", "5111", "basmati rice 5 kg", 1899),
    ("rice", "5112", "jasmine rice 32 oz", 499),
    ("eggs", "0711", "large eggs 12ct", 429),
    ("eggs", "0712", "large eggs 18 count", 599),
    ("eggs", "0713", "free range eggs 6 pk", 349),
    ("chips", "7001", "tortilla chips 13oz", 449),
    ("chips", "7002", "tortilla chips 2x200g", 499),
    ("chips", "7003", "tortilla chips family size", 549),
    ("coffee", "7501", "ground coffee 340 g", 899),
    ("coffee", "7502", "ground coffee 1.5 lb", 1499),
    ("coffee", "7503", "ground coffee 12oz", 799),
]
COLUMNS = ["category", "sku", "label", "price_cents", "base_qty", "unit", "per", "tenth_cents_per", "value_rank"]
INT_COLUMNS = {"price_cents", "base_qty", "per", "tenth_cents_per", "value_rank"}


def _sort(rows: list[tuple]) -> list[tuple]:
    """Engine-independent order (NULL ordering differs between BigQuery and DuckDB)."""
    return sorted(rows, key=lambda r: (r[0], r[5] or "~", r[8] or 0, r[1]))


def query_template() -> str:
    return "\n".join(l for l in (HERE / "unit_prices.sql").read_text().splitlines()
                     if not l.lstrip().startswith("--")).strip()


def udf_body() -> str:
    body = (HERE / "parse_pack.py").read_text()
    assert "'''" not in body, "the UDF body goes inside r'''...'''"
    return body


def bigquery_sql(part: str, target: str = "DATASET") -> str:
    if part == "udf":
        return (f"CREATE OR REPLACE FUNCTION `{target}`.parse_pack(label STRING)\n"
                f"RETURNS STRUCT<base_qty INT64, unit STRING>\nLANGUAGE python\n"
                f"OPTIONS (runtime_version = \"python-3.11\", entry_point = \"parse_pack\")\n"
                f"AS r'''\n{udf_body()}'''")
    if part == "setup":
        rows = ",\n  ".join(f"STRUCT('{c}' AS category, '{s}' AS sku, '{l}' AS label, {p} AS price_cents)"
                            for c, s, l, p in ITEMS)
        return f"CREATE OR REPLACE TABLE `{target}`.pack_items AS\nSELECT * FROM UNNEST([\n  {rows}\n])"
    if part == "query":
        return query_template().replace("{fn}", f"`{target}`.parse_pack").replace("{table}", f"`{target}`.pack_items")
    raise ValueError(part)


# ---------- reference model (plain Python running the same parse_pack code)
def pure_python() -> list[tuple]:
    priced = []
    for c, s, l, p in ITEMS:
        pk = parse_pack(l)
        per = 1 if pk["unit"] == "ct" else 100
        raw = p * 10.0 * per / pk["base_qty"] if pk["unit"] else None
        priced.append([c, s, l, p, pk["base_qty"], pk["unit"], per, None if raw is None else math.floor(raw + 0.5),
                       None, raw])
    groups: dict[tuple, list] = {}
    for r in priced:
        if r[5]:
            groups.setdefault((r[0], r[5]), []).append(r)
    for g in groups.values():
        for i, r in enumerate(sorted(g, key=lambda r: (r[9], r[1])), 1):
            r[8] = i
    return _sort([tuple(r[:9]) for r in priced])


# ---------- local: DuckDB runs unit_prices.sql with parse_pack registered as a Python function
def run_local() -> list[tuple]:
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE pack_items (category VARCHAR, sku VARCHAR, label VARCHAR, price_cents BIGINT)")
    con.executemany("INSERT INTO pack_items VALUES (?, ?, ?, ?)", ITEMS)
    con.create_function("parse_pack", parse_pack, ["VARCHAR"],
                        duckdb.struct_type({"base_qty": "BIGINT", "unit": "VARCHAR"}), null_handling="special")
    sql = query_template().replace("{fn}", "parse_pack").replace("{table}", "pack_items").replace("INT64", "BIGINT")
    return _sort([tuple(None if v is None else (int(v) if COLUMNS[i] in INT_COLUMNS else v) for i, v in enumerate(r))
                  for r in con.execute(sql).fetchall()])


# ---------- cloud output
def _redact(text: str) -> str:
    # project numbers and other long digit runs too (a "service not enabled" error carries the project number)
    return __import__("re").sub(r"\d{6,}", "<number>", text.replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>"))


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
            return _sort([tuple(None if r.get(c) is None else (int(r[c]) if c in INT_COLUMNS else r[c])
                                for c in COLUMNS) for r in obj])
    print(f"::error title=Unreadable BigQuery output::{path.name}: {_redact(text[:400])!r}")
    raise SystemExit(1)


def compare(cloud: list[tuple], local: list[tuple]) -> list[str]:
    if cloud == local:
        return []
    only_c, only_l = sorted(set(cloud) - set(local), key=str), sorted(set(local) - set(cloud), key=str)
    return [f"{len(cloud)} rows vs {len(local)} local; only cloud {only_c[:3]}, only local {only_l[:3]}"]


def summarise(rows: list[tuple]) -> dict:
    best = [dict(zip(COLUMNS, r)) for r in rows if r[8] == 1]
    unparsed = [r[2] for r in rows if r[5] is None]
    return {
        "items": len(rows), "parsed": len(rows) - len(unparsed), "unparsed": unparsed,
        "best_value": [{"category": b["category"], "label": b["label"],
                        "price": f"{b['tenth_cents_per'] / 10:.1f}c per {'item' if b['unit'] == 'ct' else '100 ' + b['unit']}"}
                       for b in best],
        "rows": [dict(zip(COLUMNS, r)) for r in rows],
        "headline": f"{len(rows) - len(unparsed)} of {len(rows)} free-text pack sizes parsed by the Python UDF; best "
                    f"value in {len(best)} categories (e.g. milk: "
                    + next(f"{b['label']} {b['tenth_cents_per'] / 10:.1f}c/100 ml" for b in best if b["category"] == "milk")
                    + ")",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(bigquery_sql(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "DATASET"))
    elif mode == "--compare":
        cloud = read_rows(Path(sys.argv[2]))
        # the GitHub Actions GCP job has no DuckDB: compare with the plain-Python model, which the local test
        # checks is identical to the DuckDB run of unit_prices.sql
        errors = compare(cloud, pure_python())
        if errors:
            print(f"::error title=Day 19 Python UDF result differs::{_redact(json.dumps(errors))[:850]}")
            raise SystemExit(1)
        s = summarise(cloud)
        print(f"::notice title=Google Cloud day 19::Python UDF parse_pack ran in BigQuery and matched the local model "
              f"(= DuckDB): {s['headline']}; best value {json.dumps([b['label'] for b in s['best_value']])[:400]}")
    else:
        rows = run_local()
        s = summarise(rows)
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB with parse_pack as a Python function (local) / "
                                                      "BigQuery Python UDF (cloud)",
                                                      **{k: v for k, v in s.items() if k != "rows"}}, indent=2) + "\n")
        print(json.dumps({k: v for k, v in s.items() if k != "rows"}, indent=2))
