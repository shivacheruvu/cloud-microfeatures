"""Day 20 (Databricks): unique barcodes. The product catalog keeps a surrogate primary key and declares the barcode
UNIQUE (informational UNIQUE constraints, Public Preview, Databricks SQL 2026 / DBR 18.2+); register scans reference
that barcode with a FOREIGN KEY. Databricks records but does not enforce these constraints, so the feature ships the
audit that does: duplicate barcodes, orphan scans, and what a duplicate costs a sales report that joins on barcode.

  python barcodes.py --local        # DuckDB: same data, same audit SQL; writes result.json
  python barcodes.py --databricks   # serverless SQL warehouse via the Statement Execution API: scratch schema with
                                    # the constraints, audit, information-schema check, compare, drop everything

The cloud check (errors):
  * a CREATE TABLE with UNIQUE / FOREIGN KEY ... REFERENCES (upc) that fails
  * the duplicate-barcode insert rejected (the constraint is informational, so it must be accepted)
  * audit rows that differ from the local DuckDB run
  * the information schema missing the UNIQUE constraint, or the foreign key not pointing at it
Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed about
the workspace except the query results.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "microfeatures_day20"

# (product_id, upc, name). Loose produce has no barcode; two items were listed twice by a supplier feed.
PRODUCTS = [
    (1, "011110038364", "whole milk 1gal"),
    (2, "011110038371", "2% milk 1gal"),
    (3, "041303001127", "large eggs 12ct"),
    (4, "070470003023", "greek yogurt 32oz"),
    (5, None, "bananas (lb)"),
    (6, None, "honeycrisp apples (lb)"),
    (7, "072250011372", "sourdough loaf"),
    (8, "041303001127", "large eggs 12ct (supplier relist)"),   # same barcode as 3
    (9, "036632001245", "orange juice 52oz"),
    (10, "072250011372", "sourdough loaf - bakery feed"),       # same barcode as 7
]

# (scan_id, upc, qty, price). Produce is weighed (no barcode); one scan has a barcode the catalog doesn't know.
SCANS = [
    (101, "011110038364", 2, "3.49"), (102, "041303001127", 1, "4.29"), (103, None, 3, "0.59"),
    (104, "070470003023", 1, "5.99"), (105, "072250011372", 1, "4.99"), (106, "041303001127", 2, "4.29"),
    (107, "036632001245", 1, "3.79"), (108, None, 2, "1.99"), (109, "011110038371", 1, "3.39"),
    (110, "085239311004", 1, "2.49"),   # unknown barcode: orphan
    (111, "072250011372", 2, "4.99"), (112, "011110038364", 1, "3.49"),
]


def sql_blocks(schema: str = SCHEMA) -> dict[str, str]:
    out = {}
    for chunk in (HERE / "keys.sql").read_text().split("-- name:")[1:]:
        lines = chunk.strip().splitlines()
        body = "\n".join(l for l in lines[1:] if not l.lstrip().startswith("--")).strip().rstrip(";")
        out[lines[0].strip()] = body.replace("{schema}", schema)
    return out


def products_insert(rows=PRODUCTS, schema: str = SCHEMA) -> str:
    return f"INSERT INTO {schema}.products VALUES " + ", ".join(
        f"({i}, {'NULL' if u is None else repr(u)}, {n!r})" for i, u, n in rows)


def scans_insert(schema: str = SCHEMA) -> str:
    return f"INSERT INTO {schema}.scans VALUES " + ", ".join(
        f"({i}, {'NULL' if u is None else repr(u)}, {q}, {p})" for i, u, q, p in SCANS)


AUDITS = ("duplicate_upcs", "orphan_scans", "join_fanout")


def normalise(name: str, rows: list) -> list[tuple]:
    """Engine rows (DuckDB tuples or API string lists) -> comparable tuples."""
    out = []
    for r in rows:
        if name == "duplicate_upcs":
            out.append((r[0], int(r[1]), int(r[2]), int(r[3])))
        elif name == "orphan_scans":
            out.append((int(r[0]), r[1]))
        else:
            out.append((int(r[0]), int(r[1]), Decimal(str(r[2])), Decimal(str(r[3]))))
    return out


# ---------- reference model (plain Python, used by the tests)
def pure_python() -> dict:
    by_upc: dict[str, list[int]] = {}
    for i, u, _ in PRODUCTS:
        if u is not None:
            by_upc.setdefault(u, []).append(i)
    dups = [(u, len(ids), min(ids), max(ids)) for u, ids in sorted(by_upc.items()) if len(ids) > 1]
    orphans = [(s, u) for s, u, _, _ in SCANS if u is not None and u not in by_upc]
    barcode_scans = sum(1 for _, u, _, _ in SCANS if u is not None)
    joined = sum(len(by_upc.get(u, [])) for _, u, _, _ in SCANS if u is not None)
    true_sales = sum(q * Decimal(p) for _, _, q, p in SCANS)
    joined_sales = sum(q * Decimal(p) * max(1, len(by_upc.get(u, []))) for _, u, q, p in SCANS)
    return {"duplicate_upcs": dups, "orphan_scans": orphans,
            "join_fanout": [(barcode_scans, joined, true_sales, joined_sales)]}


# ---------- local: DuckDB, same audit SQL
def run_local() -> dict:
    import duckdb
    con = duckdb.connect()
    con.execute(f"CREATE SCHEMA {SCHEMA}")
    b = sql_blocks()
    # DuckDB would enforce UNIQUE and reject the supplier relists, so the audit tables are created without constraints
    con.execute(f"CREATE TABLE {SCHEMA}.products (product_id BIGINT NOT NULL, upc VARCHAR, name VARCHAR)")
    con.execute(f"CREATE TABLE {SCHEMA}.scans (scan_id BIGINT NOT NULL, upc VARCHAR, qty INT, price DECIMAL(6, 2))")
    con.execute(products_insert())
    con.execute(scans_insert())
    return {name: normalise(name, con.execute(b[name]).fetchall()) for name in AUDITS}


def enforced_engine_rejects() -> list[int]:
    """Product IDs an enforcing engine (DuckDB with a real UNIQUE) refuses, inserting the catalog row by row."""
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE products (product_id BIGINT PRIMARY KEY, upc VARCHAR UNIQUE, name VARCHAR)")
    rejected = []
    for row in PRODUCTS:
        try:
            con.execute("INSERT INTO products VALUES (?, ?, ?)", row)
        except duckdb.ConstraintException:
            rejected.append(row[0])
    return rejected


# ---------- Databricks SQL
def _api(method: str, path: str, body: dict | None = None) -> dict:
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    req = urllib.request.Request(host + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        print(f"::error title=Databricks API {e.code}::{method} {path.split('?')[0]}: {e.read()[:300]!r}")
        raise SystemExit(1)


def _warehouse() -> str:
    warehouses = _api("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
    if not warehouses:
        print("::error title=Databricks day 20::No SQL warehouse in the workspace")
        raise SystemExit(1)
    return next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])["id"]


def execute(wh: str, sql: str) -> tuple[str, str, list[list]]:
    """-> (state, error message, rows)."""
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    msg = res["status"].get("error", {}).get("message", "")[:500].replace("\n", " ")
    return res["status"]["state"], msg, res.get("result", {}).get("data_array", []) or []


def statement(wh: str, sql: str, name: str, fail: bool = True) -> list[list]:
    state, msg, rows = execute(wh, sql)
    if state != "SUCCEEDED":
        print(f"::{'error' if fail else 'warning'} title=Databricks day 20 {name} {state}::{msg}")
        if fail:
            raise SystemExit(1)
    return rows


def run_databricks() -> dict:
    wh = _warehouse()
    b = sql_blocks()
    statement(wh, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}", "create schema")
    try:
        for t in ("scans", "products"):   # child first: the foreign key depends on the parent's UNIQUE key
            statement(wh, f"DROP TABLE IF EXISTS {SCHEMA}.{t}", f"drop old {t}")
        statement(wh, b["create_products"], "CREATE TABLE products with UNIQUE (upc)")
        statement(wh, b["create_scans"], "CREATE TABLE scans with FOREIGN KEY REFERENCES products (upc)")
        statement(wh, products_insert(), "insert catalog incl. duplicate barcodes (informational UNIQUE)")
        statement(wh, scans_insert(), "insert scans incl. an orphan barcode")
        res = {name: normalise(name, statement(wh, b[name], name)) for name in AUDITS}
        res["declared"] = [tuple(r) for r in statement(wh, b["declared_constraints"], "information_schema")]
        res["fk_targets"] = [tuple(r) for r in statement(wh, b["fk_targets"], "referential_constraints")]
        return res
    finally:  # leave nothing behind
        for t in ("scans", "products"):
            statement(wh, f"DROP TABLE IF EXISTS {SCHEMA}.{t}", f"drop {t}", fail=False)
        statement(wh, f"DROP SCHEMA IF EXISTS {SCHEMA}", "drop schema", fail=False)


# ---------- comparison and summary
EXPECTED_CONSTRAINTS = {("products", "products_pk", "PRIMARY KEY"), ("products", "products_upc_uq", "UNIQUE"),
                        ("scans", "scans_pk", "PRIMARY KEY"), ("scans", "scans_upc_fk", "FOREIGN KEY")}


def compare(cloud: dict, local: dict) -> list[str]:
    errors = []
    for name in AUDITS:
        if cloud[name] != local[name]:
            errors.append(f"{name}: cloud {cloud[name]} vs local {local[name]}")
    return [e.replace("Decimal(", "(") for e in errors]


def check_catalog(cloud: dict) -> list[str]:
    errors = []
    declared = {(t, c, k.upper()) for t, c, k in cloud.get("declared", [])}
    errors += [f"constraint {c} ({k}) on {t} not in information_schema" for t, c, k in
               sorted(EXPECTED_CONSTRAINTS - declared)]
    if ("scans_upc_fk", "products_upc_uq") not in {tuple(r) for r in cloud.get("fk_targets", [])}:
        errors.append(f"scans_upc_fk does not reference products_upc_uq: {cloud.get('fk_targets')}")
    return errors


def summarise(res: dict, rejected_by_enforcing_engine: list[int]) -> dict:
    names = {i: n for i, _, n in PRODUCTS}
    scans, joined, true_sales, joined_sales = res["join_fanout"][0]
    dups = [{"upc": u, "listings": n, "product_ids": [a, z], "names": [names[a], names[z]]}
            for u, n, a, z in res["duplicate_upcs"]]
    over = joined_sales - true_sales
    return {
        "products": len(PRODUCTS), "products_without_barcode": sum(1 for _, u, _ in PRODUCTS if u is None),
        "scans": len(SCANS), "barcode_scans": scans,
        "duplicate_barcodes": dups,
        "orphan_scans": [{"scan_id": s, "upc": u} for s, u in res["orphan_scans"]],
        "joined_rows": joined,
        "true_sales": str(true_sales), "sales_after_barcode_join": str(joined_sales), "overstated_by": str(over),
        "enforcing_engine_rejects_product_ids": rejected_by_enforcing_engine,
        "headline": f"{len(dups)} barcodes listed twice in the catalog: joining {scans} barcode scans on the "
                    f"UNIQUE key gives {joined} rows and overstates sales by ${over} "
                    f"(${joined_sales} vs ${true_sales}); {len(res['orphan_scans'])} scan breaks the FOREIGN KEY",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--databricks":
        local = run_local()
        cloud = run_databricks()
        errors = compare(cloud, local) + check_catalog(cloud)
        if errors:
            print(f"::error title=Databricks day 20 differs from local::{len(errors)} problem(s): "
                  f"{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud, enforced_engine_rejects())
        print("::notice title=Databricks day 20::UNIQUE (upc) and FOREIGN KEY ... REFERENCES products (upc) were "
              "created on a serverless SQL warehouse, recorded in information_schema (the FK points at "
              "products_upc_uq), the duplicate barcodes were accepted (informational), and the audit matched local "
              f"DuckDB: {s['headline']}")
    else:
        summary = summarise(run_local(), enforced_engine_rejects())
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB (local) / Databricks SQL informational "
                                                      "UNIQUE + FOREIGN KEY constraints (cloud)", **summary},
                                                     indent=2) + "\n")
        print(json.dumps(summary, indent=2))
