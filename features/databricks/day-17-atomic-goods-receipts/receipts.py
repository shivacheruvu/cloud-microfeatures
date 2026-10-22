"""Day 17 (Databricks): atomic goods receipts. Each supplier delivery is booked as ONE transaction with BEGIN ATOMIC
(multi-statement transactions, GA 2026-10-14): write the receipt lines, check them, add them to stock. A bad
delivery (a zero quantity, an unknown SKU, a resent delivery note) raises SIGNAL and rolls back, so a half-booked
delivery can never reach stock on hand.

  python receipts.py --local        # DuckDB BEGIN TRANSACTION / ROLLBACK with the same checks; writes result.json
  python receipts.py --sql D2       # print the Databricks BEGIN ATOMIC block for one delivery
  python receipts.py --databricks   # serverless SQL warehouse via the Statement Execution API: scratch schema,
                                    # six deliveries, compare with local, drop everything

The cloud check (errors):
  * a delivery that committed in one engine and was rejected in the other, or rejected for another reason
  * final stock on hand or receipt lines that differ from the local run
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "microfeatures_day17"

STOCK = [("4011", "bananas (lb)", 60), ("3283", "honeycrisp apples (lb)", 45), ("4225", "avocados (each)", 30),
         ("0702", "whole milk 1gal", 18), ("0703", "greek yogurt 32oz", 12), ("0705", "butter 1lb", 20),
         ("0711", "large eggs 12ct", 24), ("0901", "vanilla ice cream 48oz", 10)]

# (delivery_id, supplier, [(sku, qty), ...]) in the order the dock books them
DELIVERIES = [
    ("D1", "dairy co-op", [("0702", 24), ("0703", 36), ("0705", 12)]),
    ("D2", "produce", [("4011", 40), ("3283", 30), ("4225", 0)]),     # avocados typed as 0: whole delivery rejected
    ("D3", "dairy co-op", [("0702", 24), ("0711", 30)]),
    ("D4", "frozen", [("0901", 18), ("0999", 12)]),                   # 0999 isn't a SKU this store stocks
    ("D1", "dairy co-op", [("0702", 24), ("0703", 36), ("0705", 12)]),  # the D1 note sent again
    ("D6", "produce", [("4011", 40), ("3283", 30), ("4225", 20)]),    # corrected produce delivery
]


def sql_blocks(schema: str = SCHEMA) -> dict[str, str]:
    out = {}
    for chunk in (HERE / "receipts.sql").read_text().split("-- name:")[1:]:
        lines = chunk.strip().splitlines()
        body = "\n".join(l for l in lines[1:] if not l.lstrip().startswith("--")).strip().rstrip(";")
        out[lines[0].strip()] = body.replace("{schema}", schema)
    return out


def lines_sql(delivery: str, lines: list[tuple[str, int]]) -> str:
    return ", ".join(f"('{delivery}', {i}, '{sku}', {qty})" for i, (sku, qty) in enumerate(lines, start=1))


def atomic_block(delivery: str, lines: list[tuple[str, int]], schema: str = SCHEMA) -> str:
    b = sql_blocks(schema)
    return (b["atomic"].replace("{check_duplicate}", b["check_duplicate"]).replace("{check_lines}", b["check_lines"])
            .replace("{book_stock}", b["book_stock"]).replace("{lines}", lines_sql(delivery, lines))
            .replace("{delivery}", delivery))


def stock_insert(schema: str = SCHEMA) -> str:
    return f"INSERT INTO {schema}.stock VALUES " + ", ".join(f"('{s}', '{p}', {q})" for s, p, q in STOCK)


# ---------- reference model (plain Python, used by the tests)
def pure_python() -> dict:
    stock = {s: q for s, _, q in STOCK}
    receipts, outcomes = [], []
    for d, supplier, lines in DELIVERIES:
        if any(r[0] == d for r in receipts):
            outcomes.append({"delivery": d, "supplier": supplier, "status": "rejected", "reason": "already booked"})
            continue
        if any(q <= 0 or s not in stock for s, q in lines):
            outcomes.append({"delivery": d, "supplier": supplier, "status": "rejected", "reason": "bad line"})
            continue
        for i, (s, q) in enumerate(lines, start=1):
            receipts.append((d, i, s, q))
            stock[s] += q
        outcomes.append({"delivery": d, "supplier": supplier, "status": "committed", "reason": None})
    return {"outcomes": outcomes, "stock": dict(sorted(stock.items())), "receipts": sorted(receipts)}


def reason_from(message: str) -> str | None:
    return "already booked" if "already booked" in message else "bad line" if "bad line" in message else None


# ---------- local: DuckDB with explicit BEGIN / ROLLBACK / COMMIT and the same SQL checks
def run_local() -> dict:
    import duckdb
    con = duckdb.connect()
    con.execute(f"CREATE SCHEMA {SCHEMA}")
    b = sql_blocks()
    for name in ("create_stock", "create_receipts"):
        con.execute(b[name].split("TBLPROPERTIES")[0])  # DuckDB has no table properties
    con.execute(stock_insert())
    outcomes = []
    for d, supplier, lines in DELIVERIES:
        con.execute("BEGIN TRANSACTION")
        reason = None
        if con.execute(b["check_duplicate"].replace("{delivery}", d)).fetchone()[0] > 0:
            reason = "already booked"
        else:
            con.execute(f"INSERT INTO {SCHEMA}.receipts VALUES {lines_sql(d, lines)}")
            if con.execute(b["check_lines"].replace("{delivery}", d)).fetchone()[0] > 0:
                reason = "bad line"
        if reason:
            con.execute("ROLLBACK")
        else:
            con.execute(b["book_stock"].replace("{delivery}", d))
            con.execute("COMMIT")
        outcomes.append({"delivery": d, "supplier": supplier, "status": "rejected" if reason else "committed",
                         "reason": reason})
    return {"outcomes": outcomes, **snapshot(lambda q: con.execute(q).fetchall())}


def snapshot(query) -> dict:
    stock = {r[0]: int(r[1]) for r in query(f"SELECT sku, on_hand FROM {SCHEMA}.stock ORDER BY sku")}
    receipts = sorted((r[0], int(r[1]), r[2], int(r[3])) for r in
                      query(f"SELECT delivery_id, line, sku, qty FROM {SCHEMA}.receipts"))
    return {"stock": stock, "receipts": receipts}


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
        print("::error title=Databricks day 17::No SQL warehouse in the workspace")
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
        print(f"::{'error' if fail else 'warning'} title=Databricks day 17 {name} {state}::{msg}")
        if fail:
            raise SystemExit(1)
    return rows


def run_databricks() -> dict:
    wh = _warehouse()
    b = sql_blocks()
    statement(wh, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}", "create schema")
    try:
        for t in ("stock", "receipts"):
            statement(wh, f"DROP TABLE IF EXISTS {SCHEMA}.{t}", f"drop old {t}")
        statement(wh, b["create_stock"], "create stock")
        statement(wh, b["create_receipts"], "create receipts")
        statement(wh, stock_insert(), "seed stock")
        outcomes = []
        for d, supplier, lines in DELIVERIES:
            state, msg, _ = execute(wh, atomic_block(d, lines))
            reason = reason_from(msg) if state != "SUCCEEDED" else None
            if state != "SUCCEEDED" and reason is None:  # failed for some other reason: show it
                print(f"::error title=Databricks day 17 {d} {state}::{msg}")
                raise SystemExit(1)
            outcomes.append({"delivery": d, "supplier": supplier, "status": "rejected" if reason else "committed",
                             "reason": reason})
        return {"outcomes": outcomes, **snapshot(lambda q: statement(wh, q, "snapshot"))}
    finally:  # leave nothing behind
        for t in ("stock", "receipts"):
            statement(wh, f"DROP TABLE IF EXISTS {SCHEMA}.{t}", f"drop {t}", fail=False)
        statement(wh, f"DROP SCHEMA IF EXISTS {SCHEMA}", "drop schema", fail=False)


# ---------- comparison and summary
def compare(cloud: dict, local: dict) -> list[str]:
    errors = []
    for c, l in zip(cloud["outcomes"], local["outcomes"]):
        if (c["status"], c["reason"]) != (l["status"], l["reason"]):
            errors.append(f"{l['delivery']}: {c['status']}/{c['reason']} vs {l['status']}/{l['reason']}")
    if len(cloud["outcomes"]) != len(local["outcomes"]):
        errors.append(f"{len(cloud['outcomes'])} deliveries vs {len(local['outcomes'])}")
    for sku in sorted(set(cloud["stock"]) | set(local["stock"])):
        if cloud["stock"].get(sku) != local["stock"].get(sku):
            errors.append(f"stock {sku}: {cloud['stock'].get(sku)} vs {local['stock'].get(sku)}")
    c_rec, l_rec = set(map(tuple, cloud["receipts"])), set(map(tuple, local["receipts"]))
    errors += [f"receipt {r} only in cloud" for r in sorted(c_rec - l_rec)]
    errors += [f"receipt {r} only in local" for r in sorted(l_rec - c_rec)]
    return errors


def summarise(res: dict) -> dict:
    before = {s: q for s, _, q in STOCK}
    names = {s: p for s, p, _ in STOCK}
    committed = [o["delivery"] for o in res["outcomes"] if o["status"] == "committed"]
    rejected = [o for o in res["outcomes"] if o["status"] == "rejected"]
    # what a non-transactional loader (insert line by line, stop at the first bad line) would have let through
    leaked = 0
    for (d, _, lines), o in zip(DELIVERIES, res["outcomes"]):
        if o["reason"] == "bad line":
            for s, q in lines:
                if q <= 0 or s not in before:
                    break
                leaked += q
    table = [{"sku": s, "product": names[s], "before": before[s], "after": res["stock"][s],
              "received": res["stock"][s] - before[s]} for s in sorted(before)]
    units = sum(r[3] for r in res["receipts"])
    return {
        "deliveries": len(res["outcomes"]), "committed": committed,
        "rejected": [{"delivery": o["delivery"], "supplier": o["supplier"], "reason": o["reason"]} for o in rejected],
        "receipt_lines": len(res["receipts"]), "units_booked": units,
        "units_kept_out_by_rollback": leaked,
        "stock": table,
        "headline": f"{len(committed)} of {len(res['outcomes'])} deliveries booked ({units} units); "
                    f"{len(rejected)} rejected and rolled back whole, keeping {leaked} units of half-booked "
                    f"deliveries out of stock",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        want = sys.argv[2] if len(sys.argv) > 2 else "D2"
        d, _, lines = next(x for x in DELIVERIES if x[0] == want)
        print(atomic_block(d, lines) + ";")
    elif mode == "--databricks":
        local = run_local()
        cloud = run_databricks()
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{len(errors)} difference(s): "
                  f"{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud)
        per = {o["delivery"] + ("" if i != 4 else " (resent)"): o["reason"] or "committed"
               for i, o in enumerate(cloud["outcomes"])}
        print("::notice title=Databricks day 17::BEGIN ATOMIC deliveries ran on a serverless SQL warehouse and "
              f"matched the local DuckDB transactions: {s['headline']}; per delivery {json.dumps(per)}")
    else:
        summary = summarise(run_local())
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB BEGIN/ROLLBACK/COMMIT (local) / Databricks "
                                                      "SQL BEGIN ATOMIC with SIGNAL (cloud)", **summary},
                                                     indent=2) + "\n")
        print(json.dumps(summary, indent=2))
