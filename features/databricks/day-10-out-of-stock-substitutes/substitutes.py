"""Day 10 (Databricks): the two closest in-stock substitutes for every out-of-stock grocery item, with the new
NEAREST BY join (Databricks SQL 2026.20).

  python substitutes.py --local        # DuckDB: score the whole cross join and rank (the pre-NEAREST BY way);
                                       # writes result.json
  python substitutes.py --sql          # print the Databricks statement
  python substitutes.py --databricks   # serverless SQL warehouse via the Statement Execution API: run the NEAREST BY
                                       # query, compare with local (nothing is created)

The cloud check:
  * errors:   any out-of-stock item whose two substitutes (sku, rank, score) differ from local, or a missing item
  * warnings: none expected (the test data has no ties at rank 2/3, so EXACT top-2 is unique)
Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed
about the workspace except the query results.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSV = HERE / "data" / "products.csv"
NO_CLOSE_MATCH = 1000   # a score this high means the substitute is from another category


def products() -> list[dict]:
    with open(CSV, newline="") as f:
        return [{**r, **{k: int(r[k]) for k in ("cents_per_100g", "pack_g", "organic", "in_stock")}}
                for r in csv.DictReader(f)]


def score(q: dict, t: dict) -> int:
    """Same expression as the SQL, in Python (used by the tests to check there are no ties)."""
    return ((0 if q["category"] == t["category"] else 1000) + abs(q["cents_per_100g"] - t["cents_per_100g"])
            + abs(q["pack_g"] - t["pack_g"]) // 10 + 50 * abs(q["organic"] - t["organic"]))


def sql_statements(prods: list[dict]) -> dict[str, str]:
    values = ",\n".join(f"    ('{p['sku']}', '{p['product']}', '{p['category']}', {p['cents_per_100g']}, "
                        f"{p['pack_g']}, {p['organic']}, {p['in_stock']})" for p in prods)
    out = {}
    for chunk in (HERE / "substitutes.sql").read_text().split(";"):
        lines = chunk.strip().splitlines()
        if not lines:
            continue
        name = lines[0].split(":", 1)[1].strip()
        body = "\n".join(l for l in lines if not l.lstrip().startswith("--"))
        out[name] = body.replace("{products}", values).replace("{csv}", str(CSV))
    return out


def rows_to_result(rows: list[list]) -> list[dict]:
    return [{"oos_sku": r[0], "oos_product": r[1], "rank": int(r[2]), "sub_sku": r[3], "sub_product": r[4],
             "score": int(r[5])} for r in rows]


# ---------- local
def run_local(prods: list[dict]) -> list[dict]:
    import duckdb
    con = duckdb.connect()
    return rows_to_result(con.execute(sql_statements(prods)["local"]).fetchall())


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
        print("::error title=Databricks day 10::No SQL warehouse in the workspace")
        raise SystemExit(1)
    return next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])["id"]


def statement(wh: str, sql: str) -> list[list]:
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")[:500].replace("\n", " ")
        print(f"::error title=Databricks day 10 {res['status']['state']}::{msg}")
        raise SystemExit(1)
    return res.get("result", {}).get("data_array", []) or []


def run_databricks(prods: list[dict]) -> list[dict]:
    return rows_to_result(statement(_warehouse(), sql_statements(prods)["databricks"]))


# ---------- comparison and summary
KEYS = ("rank", "sub_sku", "score")


def compare(cloud: list[dict], local: list[dict]) -> list[str]:
    def by_item(rows):
        out: dict[str, list] = {}
        for r in rows:
            out.setdefault(r["oos_sku"], []).append(tuple(r[k] for k in KEYS))
        return {k: sorted(v) for k, v in out.items()}
    c, l = by_item(cloud), by_item(local)
    errors = [f"{sku}: cloud {c.get(sku)} local {subs}" for sku, subs in l.items() if c.get(sku) != subs]
    errors += [f"{sku}: in cloud only" for sku in c if sku not in l]
    return errors


def summarise(rows: list[dict]) -> dict:
    items: dict[str, dict] = {}
    for r in rows:
        it = items.setdefault(r["oos_sku"], {"out_of_stock": r["oos_product"], "substitutes": []})
        it["substitutes"].append({"rank": r["rank"], "sku": r["sub_sku"], "product": r["sub_product"],
                                  "score": r["score"]})
    table = list(items.values())
    no_close = [t["out_of_stock"] for t in table if t["substitutes"][0]["score"] >= NO_CLOSE_MATCH]
    weak_second = [t["out_of_stock"] for t in table
                   if len(t["substitutes"]) > 1 and t["substitutes"][1]["score"] >= NO_CLOSE_MATCH]
    first = [t["substitutes"][0]["score"] for t in table]
    return {
        "out_of_stock": len(table),
        "substitutes": len(rows),
        "same_category_first_choice": len(table) - len(no_close),
        "second_choice_from_other_category": weak_second,
        "median_first_choice_score": sorted(first)[len(first) // 2],
        "table": table,
        "headline": f"{len(table)} out-of-stock items, 2 substitutes each: {len(table) - len(no_close)} first "
                    f"choices from the same category, {len(weak_second)} second choice from another category",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    prods = products()
    if mode == "--sql":
        print(sql_statements(prods)["databricks"] + ";")
    elif mode == "--databricks":
        local = run_local(prods)
        cloud = run_databricks(prods)
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{json.dumps(errors)[:900]}")
            raise SystemExit(1)
        s = summarise(cloud)
        pairs = [[t["out_of_stock"]] + [f"{x['product']} ({x['score']})" for x in t["substitutes"]]
                 for t in s["table"]]
        print("::notice title=Databricks day 10::EXACT NEAREST 2 BY DISTANCE ran on a serverless SQL warehouse and "
              f"matched the local cross-join ranking: {s['headline']}; {json.dumps(pairs)[:600]}")
    else:
        summary = summarise(run_local(prods))
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB cross join + row_number (local) / "
                                                      "Databricks SQL NEAREST BY join (cloud)", **summary},
                                                     indent=2) + "\n")
        print(json.dumps(summary, indent=2))
