"""Day 15 (Databricks, Free Edition): re-stage a corrected supplier cost file with CREATE OR REPLACE TEMP TABLE.
A supplier sends today's unit costs, then re-sends a corrected file. Each delivery is staged in a session-local
temporary table; the re-delivery replaces it in one statement (new in Databricks SQL 2026.15), and the margin check
runs against the newest file only. One SQL script (BEGIN ... END) keeps everything in one session; temporary tables
disappear when it ends, so nothing is created in the catalog.

  python restage.py --make-data    # regenerate data/*.csv (deterministic)
  python restage.py --local        # plain Python does the same; writes result.json
  python restage.py --sql          # print the Databricks SQL script
  python restage.py --databricks   # serverless SQL warehouse via the Statement Execution API; compare with local

The cloud check:
  * errors: a missing, extra or different row, or any row from the first (replaced) delivery
Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed about
the workspace except the query results. SKUs, costs and prices are made up.
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FILES = ("shelf", "delivery1", "delivery2")
TARGET_MARGIN = 0.25  # a SKU is "below target" when (price - cost) / price < 25%, i.e. 3 * price < 4 * cost
# sku, aisle, shelf price (cents)
SKUS = [
    ("P01", "dairy", 389), ("P02", "dairy", 549), ("P03", "dairy", 429), ("P04", "produce", 69),
    ("P05", "produce", 299), ("P06", "produce", 449), ("P07", "pantry", 329), ("P08", "pantry", 199),
    ("P09", "pantry", 479), ("P10", "bakery", 499), ("P11", "bakery", 359), ("P12", "bakery", 279),
]


def make_data() -> dict[str, list[dict]]:
    rng = random.Random(15)
    shelf = [{"sku": s, "aisle": a, "price_cents": str(p)} for s, a, p in SKUS]
    d2 = []
    for s, _a, p in SKUS:
        d2.append({"sku": s, "cost_cents": str(round(p * rng.uniform(0.62, 0.82))), "delivery": "2"})
    # the first file: one SKU missing, three costs keyed in wrong (a digit dropped or doubled)
    d1 = [dict(r, delivery="1") for r in d2 if r["sku"] != "P12"]
    for i, f in ((1, 0.1), (5, 10), (8, 0.1)):
        d1[i]["cost_cents"] = str(round(int(d1[i]["cost_cents"]) * f))
    return {"shelf": shelf, "delivery1": d1, "delivery2": d2}


def load() -> dict[str, list[dict]]:
    out = {}
    for name in FILES:
        with open(DATA / f"{name}.csv", newline="") as f:
            out[name] = list(csv.DictReader(f))
    return out


def _values(rows: list[dict]) -> str:
    def lit(v: str) -> str:
        return v if v.isdigit() else f"'{v}'"
    return ",\n".join("    (" + ", ".join(lit(v) for v in r.values()) + ")" for r in rows)


def sql_script(data: dict[str, list[dict]]) -> str:
    body = "\n".join(l for l in (HERE / "restage.sql").read_text().splitlines() if not l.lstrip().startswith("--"))
    for name in FILES:
        body = body.replace("{" + name + "}", _values(data[name]))
    return body.strip()


def suggested(cost: int) -> int:
    """Smallest price ending in 9 with at least a 25% margin."""
    x = math.ceil(cost * 4 / 3)
    return x + 9 - x % 10


# ---------- local: the same answer in plain Python (the newest delivery wins)
def run_local(data: dict[str, list[dict]]) -> list[dict]:
    batch = data["delivery2"]  # what is left after CREATE OR REPLACE
    cost = {r["sku"]: int(r["cost_cents"]) for r in batch}
    rows = []
    for s in data["shelf"]:
        if s["sku"] not in cost:
            continue
        c, p = cost[s["sku"]], int(s["price_cents"])
        below = (p - c) * 4 < p
        rows.append({"sku": s["sku"], "aisle": s["aisle"], "cost_cents": c, "price_cents": p, "delivery": 2,
                     "below_target": below, "suggested_cents": suggested(c) if below else None,
                     "batch_rows": len(batch)})
    return sorted(rows, key=lambda r: r["sku"])


def rows_to_result(rows: list[list]) -> list[dict]:
    # Statement Execution API (JSON_ARRAY): every value is a string or null
    return [{"sku": r[0], "aisle": r[1], "cost_cents": int(r[2]), "price_cents": int(r[3]), "delivery": int(r[4]),
             "below_target": str(r[5]).lower() == "true", "suggested_cents": None if r[6] is None else int(r[6]),
             "batch_rows": int(r[7])} for r in rows]


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
        print("::error title=Databricks day 15::No SQL warehouse in the workspace")
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
        print(f"::error title=Databricks day 15 {res['status']['state']}::{msg}")
        raise SystemExit(1)
    return res.get("result", {}).get("data_array", []) or []


def run_databricks(data: dict[str, list[dict]]) -> list[dict]:
    return rows_to_result(statement(_warehouse(), sql_script(data)))


# ---------- comparison and summary
def compare(cloud: list[dict], local: list[dict]) -> list[str]:
    c, l = {r["sku"]: r for r in cloud}, {r["sku"]: r for r in local}
    errors = [f"{k}: local only" for k in l if k not in c]
    errors += [f"{k}: cloud only" for k in c if k not in l]
    if len(cloud) != len(c):
        errors.append(f"cloud returned {len(cloud) - len(c)} duplicate row(s)")
    errors += [f"{r['sku']}: row from replaced delivery {r['delivery']}" for r in cloud if r["delivery"] != 2]
    for k, v in l.items():
        if k in c and c[k] != v:
            diff = {f: [c[k][f], v[f]] for f in v if c[k][f] != v[f]}
            errors.append(f"{k}: cloud vs local {json.dumps(diff)}")
    return errors


def summarise(rows: list[dict], data: dict[str, list[dict]]) -> dict:
    d1 = {r["sku"]: int(r["cost_cents"]) for r in data["delivery1"]}
    d2 = {r["sku"]: int(r["cost_cents"]) for r in data["delivery2"]}
    corrected = sorted(k for k in d2 if k in d1 and d1[k] != d2[k])
    below = [r for r in rows if r["below_target"]]
    # what the first file would have said: shows why pricing off a bad file is dangerous
    shelf = {r["sku"]: int(r["price_cents"]) for r in data["shelf"]}
    below_d1 = sorted(k for k, c in d1.items() if (shelf[k] - c) * 4 < shelf[k])
    margin = {r["sku"]: round((r["price_cents"] - r["cost_cents"]) / r["price_cents"] * 100, 1) for r in rows}
    return {
        "delivery1_rows": len(d1), "delivery2_rows": len(d2), "staged_rows_after_replace": rows[0]["batch_rows"],
        "costs_corrected": corrected, "added_in_redelivery": sorted(set(d2) - set(d1)),
        "skus_priced": len(rows), "below_target": [[r["sku"], r["cost_cents"], r["price_cents"],
                                                    r["suggested_cents"]] for r in below],
        "below_target_if_first_file_used": below_d1,
        "first_file_wrong_flags": sorted(set(below_d1) ^ {r["sku"] for r in below}),
        "lowest_margin_pct": min(margin.items(), key=lambda kv: (kv[1], kv[0])),
        "headline": f"re-delivery replaced {len(d1)} staged rows with {len(d2)} ({len(corrected)} costs corrected, "
                    f"{len(set(d2) - set(d1))} added); {len(below)} of {len(rows)} SKUs below the 25% margin target "
                    f"(pricing off the first file would have got {len(set(below_d1) ^ {r['sku'] for r in below})} flags wrong)",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        DATA.mkdir(exist_ok=True)
        for name, rows in make_data().items():
            with open(DATA / f"{name}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        raise SystemExit(0)
    data = load()
    if mode == "--sql":
        print(sql_script(data))
    elif mode == "--databricks":
        local = run_local(data)
        cloud = run_databricks(data)
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{len(errors)} difference(s): "
                  f"{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud, data)
        print("::notice title=Databricks day 15::CREATE OR REPLACE TEMP TABLE ran in a SQL script on a serverless SQL "
              f"warehouse and matched local for all {len(cloud)} SKUs, none from the replaced delivery: {s['headline']}; "
              f"below target {json.dumps(s['below_target'])}")
    else:
        summary = summarise(run_local(data), data)
        (HERE / "result.json").write_text(json.dumps({"engine": "Python (local) / Databricks SQL temp tables (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
