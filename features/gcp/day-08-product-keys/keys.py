"""Day 8 (Google Cloud): a product master whose surrogate keys BigQuery generates, with identity columns (Preview,
2026-08-31): GENERATED ALWAYS AS IDENTITY (START WITH 1000 INCREMENT BY 10).

  python keys.py --local                    # DuckDB with a sequence (START 1000 INCREMENT 10): load, add the new
                                            # feed rows, join the scans; writes result.json
  python keys.py --sql NAME DATASET         # print one BigQuery statement (create, load, add_new, explicit_key,
                                            # products, identity_info, scan_facts)
  python keys.py --compare products.json identity.json facts.json EXPLICIT_REJECTED(0|1)

BigQuery only promises identity values that are unique, loosely ordered and sparse (a multiple of the increment
apart), so the cloud check compares properties, not exact keys:
  * errors:   the set of (barcode, name) differs from local; a key is repeated, below 1000 or off the 10-step grid;
              the hand-written key was accepted; INFORMATION_SCHEMA doesn't mark the column as an identity column;
              units / scans per product differ from local, or a fact row's key isn't that product's key
  * warnings: the reported generation / start / increment don't read ALWAYS / 1000 / 10, or the
              INFORMATION_SCHEMA query returned nothing
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
START, INCREMENT = 1000, 10


def load(name: str) -> list[dict]:
    with open(HERE / "data" / f"{name}.csv", newline="") as f:
        return list(csv.DictReader(f))


def struct_list(rows: list[dict]) -> str:
    def lit(k, v):
        return f"{int(v)} AS {k}" if k == "units" else f"'{v}' AS {k}"
    return ", ".join("STRUCT(" + ", ".join(lit(k, v) for k, v in r.items()) + ")" for r in rows)


def statements(ds: str) -> dict[str, str]:
    fill = {"{ds}": ds, "{products}": struct_list(load("products")),
            "{new_products}": struct_list(load("new_products")), "{scans}": struct_list(load("scans"))}
    out = {}
    for chunk in (HERE / "keys.sql").read_text().split(";"):
        lines = chunk.strip().splitlines()
        if not lines:
            continue
        name = lines[0].split(":", 1)[1].strip()
        body = "\n".join(l for l in lines if not l.lstrip().startswith("--"))
        for k, v in fill.items():
            body = body.replace(k, v)
        out[name] = body
    return out


# ---------- local
def run_local() -> dict:
    import duckdb
    con = duckdb.connect()
    con.execute(f"CREATE SEQUENCE product_key_seq START {START} INCREMENT BY {INCREMENT}")
    con.execute("CREATE TABLE products (product_key BIGINT DEFAULT nextval('product_key_seq'), "
                "barcode VARCHAR NOT NULL, name VARCHAR)")
    con.executemany("INSERT INTO products (barcode, name) VALUES (?, ?)",
                    [[r["barcode"], r["name"]] for r in load("products")])
    first_batch = {r[0] for r in con.sql("SELECT barcode FROM products").fetchall()}
    for r in load("new_products"):
        con.execute("INSERT INTO products (barcode, name) SELECT ?, ? WHERE NOT EXISTS "
                    "(SELECT 1 FROM products WHERE barcode = ?)", [r["barcode"], r["name"], r["barcode"]])
    con.execute("CREATE TABLE scans (barcode VARCHAR, units INTEGER)")
    con.executemany("INSERT INTO scans VALUES (?, ?)", [[r["barcode"], int(r["units"])] for r in load("scans")])
    products = [{"product_key": k, "barcode": b, "name": n}
                for k, b, n in con.sql("SELECT product_key, barcode, name FROM products ORDER BY barcode").fetchall()]
    facts = [{"product_key": k, "name": n, "units": int(u), "scans": int(c)} for k, n, u, c in con.sql(
        "SELECT p.product_key, p.name, SUM(s.units), COUNT(*) FROM scans s JOIN products p USING (barcode) "
        "GROUP BY 1, 2 ORDER BY 2").fetchall()]
    return {"products": products, "facts": facts, "first_batch": sorted(first_batch)}


# ---------- BigQuery output
def read_bq(path: str) -> list[dict]:
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a warning line before the JSON
    try:
        return json.loads(text[start:]) if start >= 0 else []
    except Exception as e:  # noqa: BLE001 - any shape surprise is reported, redacted, as an annotation
        raw = text[max(start, 0):max(start, 0) + 500].replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {e}; {raw!r}")
        raise SystemExit(1)


def check_keys(keys: list[int]) -> list[str]:
    errors = []
    if len(set(keys)) != len(keys):
        errors.append(f"repeated keys: {sorted(keys)}")
    off = [k for k in keys if k < START or (k - START) % INCREMENT]
    if off:
        errors.append(f"keys below {START} or off the {INCREMENT}-step grid: {off}")
    return errors


def compare(products: list[dict], identity: list[dict], facts: list[dict], explicit_rejected: bool,
            local: dict) -> tuple[list[str], list[str], dict]:
    errors, warnings = [], []
    cloud_items = sorted((p["barcode"], p["name"]) for p in products)
    local_items = sorted((p["barcode"], p["name"]) for p in local["products"])
    if cloud_items != local_items:
        errors.append(f"products differ: {len(cloud_items)} cloud vs {len(local_items)} local rows")
    keys = [int(p["product_key"]) for p in products]
    errors += check_keys(keys)
    if not explicit_rejected:
        errors.append("a hand-written product_key was accepted by a GENERATED ALWAYS column")
    info = identity[0] if identity else {}
    if not identity:
        warnings.append("INFORMATION_SCHEMA.COLUMNS returned no identity details for product_key")
    elif str(info.get("is_identity", "")).upper() != "YES":
        errors.append(f"INFORMATION_SCHEMA.COLUMNS is_identity: {info.get('is_identity')!r}")
    seen = (str(info.get("identity_generation", "")).upper(), str(info.get("identity_start")),
            str(info.get("identity_increment")))
    if identity and seen != ("ALWAYS", str(START), str(INCREMENT)):
        warnings.append(f"identity generation/start/increment reported as {seen}")
    key_of = {p["name"]: int(p["product_key"]) for p in products}
    got = {f["name"]: (int(f["units"]), int(f["scans"])) for f in facts}
    want = {f["name"]: (f["units"], f["scans"]) for f in local["facts"]}
    if got != want:
        errors.append(f"scan facts differ: cloud {got} local {want}")
    wrong = [f["name"] for f in facts if int(f["product_key"]) != key_of.get(f["name"])]
    if wrong:
        errors.append(f"fact rows carry the wrong key: {wrong}")
    first = set(local["first_batch"])
    by_barcode = {p["barcode"]: int(p["product_key"]) for p in products}
    summary = {"products": len(products), "keys": sorted(keys),
               "dense": sorted(keys) == list(range(START, START + INCREMENT * len(keys), INCREMENT)),
               "new_in_feed": sorted(p["name"] for p in products if p["barcode"] not in first),
               "new_keys": sorted(k for b, k in by_barcode.items() if b not in first),
               "identity": dict(zip(("generation", "start", "increment"), seen)),
               "explicit_key_rejected": explicit_rejected}
    return errors, warnings, summary


def summarise(local: dict) -> dict:
    first = set(local["first_batch"])
    keys = [p["product_key"] for p in local["products"]]
    return {"start_with": START, "increment_by": INCREMENT, "products": len(local["products"]),
            "first_batch": len(first), "feed_rows": len(load("new_products")),
            "new_in_feed": sorted(p["name"] for p in local["products"] if p["barcode"] not in first),
            "keys": sorted(keys), "key_errors": check_keys(keys),
            "scan_rows": len(load("scans")), "facts": local["facts"],
            "headline": f"{len(local['products'])} products keyed {min(keys)}-{max(keys)} by BigQuery, "
                        f"{len(local['products']) - len(first)} new from the feed"}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(statements(sys.argv[3])[sys.argv[2]])
    elif mode == "--compare":
        local = run_local()
        try:
            errors, warnings, summary = compare(read_bq(sys.argv[2]), read_bq(sys.argv[3]), read_bq(sys.argv[4]),
                                                sys.argv[5] == "1", local)
        except Exception as e:  # noqa: BLE001 - report the shape problem, never project details
            print(f"::error title=Identity column output not understood::{type(e).__name__}: {e}")
            raise SystemExit(1)
        for w in warnings:
            print(f"::warning title=Identity column vs local::{w[:900]}")
        if errors:
            print(f"::error title=BigQuery identity column check failed::{json.dumps(errors)[:900]}")
            raise SystemExit(1)
        print(f"::notice title=Google Cloud day 8::GENERATED ALWAYS AS IDENTITY keyed {summary['products']} products "
              f"on BigQuery and the scan facts matched local: {json.dumps(summary)[:800]}")
    else:
        summary = summarise(run_local())
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB sequence (local) / BigQuery identity column "
                                                      "(cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
