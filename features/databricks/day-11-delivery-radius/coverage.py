"""Day 11 (Databricks): delivery coverage. Which households are within 2.5 km of each grocery store, with a spatial
join on the ST_DWithin predicate (supported as a spatial join condition since Databricks SQL 2026.15) and points from
st_makepoint (same release).

  python coverage.py --make-data    # regenerate data/stores.csv and data/homes.csv (deterministic)
  python coverage.py --local        # DuckDB cross join + distance filter; writes result.json
  python coverage.py --sql          # print the Databricks statement
  python coverage.py --databricks   # serverless SQL warehouse via the Statement Execution API: run the spatial join,
                                    # compare with local (nothing is created)

The cloud check:
  * errors:   any (home, store) pair in one result and not the other, or a distance more than 1 m apart
  * warnings: none expected (the data keeps every home at least 5 m away from a radius boundary, so float rounding
              can't move a pair in or out; a test checks it)
Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed
about the workspace except the query results.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORES_CSV = HERE / "data" / "stores.csv"
HOMES_CSV = HERE / "data" / "homes.csv"
RADIUS_M = 2500
BOUNDARY_GAP_M = 5          # no home closer than this to any store's radius boundary
GRID_M = 10_000             # a 10 km x 10 km town
STORES = [("S1", "Downtown Market", 3200, 6900), ("S2", "Riverside Fresh", 6600, 6600),
          ("S3", "Southgate Grocer", 4300, 3100), ("S4", "Eastfield Foods", 7900, 2600)]
N_HOMES = 60


def make_data() -> tuple[list[dict], list[dict]]:
    seed, homes, i = 20261015, [], 0
    while len(homes) < N_HOMES:
        seed = (seed * 1103515245 + 12345) % 2**31
        x = seed % GRID_M
        seed = (seed * 1103515245 + 12345) % 2**31
        y = seed % GRID_M
        if all(abs(math.hypot(x - sx, y - sy) - RADIUS_M) >= BOUNDARY_GAP_M for _, _, sx, sy in STORES):
            i += 1
            homes.append({"home_id": f"H{i:02d}", "x_m": x, "y_m": y})
    stores = [{"store_id": s, "store": n, "x_m": x, "y_m": y} for s, n, x, y in STORES]
    return stores, homes


def _read(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return [{k: (int(v) if k in ("x_m", "y_m") else v) for k, v in r.items()} for r in csv.DictReader(f)]


def load() -> tuple[list[dict], list[dict]]:
    return _read(STORES_CSV), _read(HOMES_CSV)


def sql_statements(stores: list[dict], homes: list[dict]) -> dict[str, str]:
    sv = ",\n".join(f"    ('{s['store_id']}', '{s['store']}', {float(s['x_m'])}D, {float(s['y_m'])}D)" for s in stores)
    hv = ",\n".join(f"    ('{h['home_id']}', {float(h['x_m'])}D, {float(h['y_m'])}D)" for h in homes)
    out = {}
    for chunk in (HERE / "coverage.sql").read_text().split(";"):
        lines = chunk.strip().splitlines()
        if not lines:
            continue
        name = lines[0].split(":", 1)[1].strip()
        body = "\n".join(l for l in lines if not l.lstrip().startswith("--"))
        out[name] = (body.replace("{stores}", sv).replace("{homes}", hv).replace("{radius}", f"{float(RADIUS_M)}")
                     .replace("{stores_csv}", str(STORES_CSV)).replace("{homes_csv}", str(HOMES_CSV)))
    return out


def rows_to_result(rows: list[list]) -> list[dict]:
    return [{"home_id": r[0], "store_id": r[1], "dist_m": int(float(r[2]))} for r in rows]


def pure_python(stores: list[dict], homes: list[dict]) -> list[dict]:
    """Same pairs in plain Python (used by the tests)."""
    out = []
    for h in homes:
        for s in stores:
            d = math.hypot(h["x_m"] - s["x_m"], h["y_m"] - s["y_m"])
            if d <= RADIUS_M:
                out.append({"home_id": h["home_id"], "store_id": s["store_id"], "dist_m": int(d + 0.5)})
    return sorted(out, key=lambda r: (r["home_id"], r["dist_m"]))


# ---------- local
def run_local(stores: list[dict], homes: list[dict]) -> list[dict]:
    import duckdb
    return rows_to_result(duckdb.connect().execute(sql_statements(stores, homes)["local"]).fetchall())


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
        print("::error title=Databricks day 11::No SQL warehouse in the workspace")
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
        print(f"::error title=Databricks day 11 {res['status']['state']}::{msg}")
        raise SystemExit(1)
    return res.get("result", {}).get("data_array", []) or []


def run_databricks(stores: list[dict], homes: list[dict]) -> list[dict]:
    return rows_to_result(statement(_warehouse(), sql_statements(stores, homes)["databricks"]))


# ---------- comparison and summary
def compare(cloud: list[dict], local: list[dict]) -> list[str]:
    c = {(r["home_id"], r["store_id"]): r["dist_m"] for r in cloud}
    l = {(r["home_id"], r["store_id"]): r["dist_m"] for r in local}
    errors = [f"{k}: local only ({v} m)" for k, v in l.items() if k not in c]
    errors += [f"{k}: cloud only ({v} m)" for k, v in c.items() if k not in l]
    errors += [f"{k}: {c[k]} m vs {v} m" for k, v in l.items() if k in c and abs(c[k] - v) > 1]
    return errors


def summarise(rows: list[dict], stores: list[dict], homes: list[dict]) -> dict:
    by_home: dict[str, list[dict]] = {}
    for r in rows:
        by_home.setdefault(r["home_id"], []).append(r)
    uncovered = sorted(h["home_id"] for h in homes if h["home_id"] not in by_home)
    multi = sorted(k for k, v in by_home.items() if len(v) > 1)
    nearest = {k: min(v, key=lambda r: r["dist_m"])["store_id"] for k, v in by_home.items()}
    table = []
    for s in stores:
        mine = [r for r in rows if r["store_id"] == s["store_id"]]
        dists = sorted(r["dist_m"] for r in mine)
        table.append({"store_id": s["store_id"], "store": s["store"], "homes_in_radius": len(mine),
                      "nearest_store_for": sum(1 for v in nearest.values() if v == s["store_id"]),
                      "only_store_for": sum(1 for k, v in by_home.items() if len(v) == 1 and v[0]["store_id"] ==
                                            s["store_id"]),
                      "median_dist_m": dists[len(dists) // 2] if dists else None})
    covered = len(by_home)
    return {
        "radius_m": RADIUS_M, "stores": len(stores), "homes": len(homes), "pairs": len(rows),
        "homes_covered": covered, "homes_uncovered": len(uncovered), "homes_with_choice": len(multi),
        "coverage_pct": round(100 * covered / len(homes), 1),
        "table": table,
        "headline": f"{covered} of {len(homes)} homes ({round(100 * covered / len(homes))}%) within {RADIUS_M / 1000:g} "
                    f"km of a store, {len(multi)} with a choice of stores, {len(uncovered)} outside every radius",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        stores, homes = make_data()
        for path, rows in ((STORES_CSV, stores), (HOMES_CSV, homes)):
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        raise SystemExit(0)
    stores, homes = load()
    if mode == "--sql":
        print(sql_statements(stores, homes)["databricks"] + ";")
    elif mode == "--databricks":
        local = run_local(stores, homes)
        cloud = run_databricks(stores, homes)
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{len(errors)} difference(s): "
                  f"{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud, stores, homes)
        per_store = {t["store"]: t["homes_in_radius"] for t in s["table"]}
        print("::notice title=Databricks day 11::Spatial join ON st_dwithin(st_makepoint(...), st_makepoint(...), "
              f"{RADIUS_M}) ran on a serverless SQL warehouse and matched the local distance filter "
              f"({s['pairs']} home-store pairs): {s['headline']}; homes per store {json.dumps(per_store)}")
    else:
        summary = summarise(run_local(stores, homes), stores, homes)
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB cross join + distance filter (local) / "
                                                      "Databricks SQL spatial join on ST_DWithin (cloud)", **summary},
                                                     indent=2) + "\n")
        print(json.dumps(summary, indent=2))
