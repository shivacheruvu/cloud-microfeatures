"""Day 16 (Databricks): flyer drop zones. Which households does each rectangular flyer zone reach, with the boxes built
by st_makeenvelope (new in Databricks SQL 2026.15) from two corners typed in any order, and homes from st_makepoint.

  python zones.py --make-data    # regenerate data/zones.csv and data/homes.csv (deterministic)
  python zones.py --local        # DuckDB min/max box filter; writes result.json
  python zones.py --sql          # print the Databricks statement
  python zones.py --databricks   # serverless SQL warehouse via the Statement Execution API: run the query,
                                 # compare with local (nothing is created)

The cloud check:
  * errors: a (zone, home) pair in one result and not the other, or a zone whose point count (5 = box, 2 = line,
            1 = point) or area differs from the local one
Homes are kept at least 5 m away from every zone edge (a test checks it), so "on the boundary" never decides a pair.
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
ZONES_CSV = HERE / "data" / "zones.csv"
HOMES_CSV = HERE / "data" / "homes.csv"
EDGE_GAP_M = 5
GRID_M = 10_000
N_HOMES = 80
COST_PER_FLYER = 0.18   # print + delivery, USD, for the "wasted" figure
# corners as the flyer team typed them: Z2 has its corners reversed, Z5 has a typo (x1 == x2), Z3 and Z4 overlap
ZONES = [("Z1", "North Hill", 1000, 6000, 4500, 9500),
         ("Z2", "Riverside", 8800, 8800, 5200, 5600),
         ("Z3", "Southgate", 2500, 1000, 7000, 4000),
         ("Z4", "Eastfield", 5500, 1500, 9500, 4500),
         ("Z5", "Mill Road", 7000, 5000, 7000, 5400)]


def _clear_of_edges(x: int, y: int) -> bool:
    xs = {v for z in ZONES for v in (z[2], z[4])}
    ys = {v for z in ZONES for v in (z[3], z[5])}
    return all(abs(x - v) >= EDGE_GAP_M for v in xs) and all(abs(y - v) >= EDGE_GAP_M for v in ys)


def make_data() -> tuple[list[dict], list[dict]]:
    seed, homes = 20261020, []
    while len(homes) < N_HOMES:
        seed = (seed * 1103515245 + 12345) % 2**31
        x = seed % GRID_M
        seed = (seed * 1103515245 + 12345) % 2**31
        y = seed % GRID_M
        if _clear_of_edges(x, y):
            homes.append({"home_id": f"H{len(homes) + 1:02d}", "x_m": x, "y_m": y})
    zones = [{"zone_id": z, "zone": n, "x1": a, "y1": b, "x2": c, "y2": d} for z, n, a, b, c, d in ZONES]
    return zones, homes


def _read(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return [{k: (v if k in ("zone_id", "zone", "home_id") else int(v)) for k, v in r.items()}
                for r in csv.DictReader(f)]


def load() -> tuple[list[dict], list[dict]]:
    return _read(ZONES_CSV), _read(HOMES_CSV)


def sql_statements(zones: list[dict], homes: list[dict]) -> dict[str, str]:
    zv = ",\n".join(f"    ('{z['zone_id']}', '{z['zone']}', {float(z['x1'])}D, {float(z['y1'])}D, {float(z['x2'])}D, "
                    f"{float(z['y2'])}D)" for z in zones)
    hv = ",\n".join(f"    ('{h['home_id']}', {float(h['x_m'])}D, {float(h['y_m'])}D)" for h in homes)
    out = {}
    for chunk in (HERE / "zones.sql").read_text().split(";"):
        lines = chunk.strip().splitlines()
        if not lines:
            continue
        name = lines[0].split(":", 1)[1].strip()
        body = "\n".join(l for l in lines if not l.lstrip().startswith("--"))
        out[name] = (body.replace("{zones}", zv).replace("{homes}", hv)
                     .replace("{zones_csv}", str(ZONES_CSV)).replace("{homes_csv}", str(HOMES_CSV)))
    return out


def rows_to_result(rows: list[list]) -> list[dict]:
    """Statement Execution API rows (all strings) or DuckDB tuples -> zone/home rows; home_id is None for no match."""
    return [{"zone_id": r[0], "npoints": int(r[1]), "area_m2": int(float(r[2])), "home_id": r[3]} for r in rows]


def pure_python(zones: list[dict], homes: list[dict]) -> list[dict]:
    """Same rows in plain Python (used by the tests)."""
    out = []
    for z in zones:
        xmin, xmax = sorted((z["x1"], z["x2"]))
        ymin, ymax = sorted((z["y1"], z["y2"]))
        npts = 1 if (xmin == xmax and ymin == ymax) else 2 if (xmin == xmax or ymin == ymax) else 5
        inside = [h["home_id"] for h in homes if xmin < h["x_m"] < xmax and ymin < h["y_m"] < ymax]
        for hid in sorted(inside) or [None]:
            out.append({"zone_id": z["zone_id"], "npoints": npts, "area_m2": (xmax - xmin) * (ymax - ymin),
                        "home_id": hid})
    return out


# ---------- local
def run_local(zones: list[dict], homes: list[dict]) -> list[dict]:
    import duckdb
    return rows_to_result(duckdb.connect().execute(sql_statements(zones, homes)["local"]).fetchall())


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
        print("::error title=Databricks day 16::No SQL warehouse in the workspace")
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
        print(f"::error title=Databricks day 16 {res['status']['state']}::{msg}")
        raise SystemExit(1)
    return res.get("result", {}).get("data_array", []) or []


def run_databricks(zones: list[dict], homes: list[dict]) -> list[dict]:
    return rows_to_result(statement(_warehouse(), sql_statements(zones, homes)["databricks"]))


# ---------- comparison and summary
def _by_zone(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        z = out.setdefault(r["zone_id"], {"npoints": r["npoints"], "area_m2": r["area_m2"], "homes": set()})
        if r["home_id"] is not None:
            z["homes"].add(r["home_id"])
    return out


def compare(cloud: list[dict], local: list[dict]) -> list[str]:
    c, l = _by_zone(cloud), _by_zone(local)
    errors = [f"{k}: zone missing from cloud" for k in l if k not in c]
    errors += [f"{k}: zone only in cloud" for k in c if k not in l]
    for k in sorted(set(c) & set(l)):
        if c[k]["npoints"] != l[k]["npoints"]:
            errors.append(f"{k}: {c[k]['npoints']} points vs {l[k]['npoints']}")
        if abs(c[k]["area_m2"] - l[k]["area_m2"]) > 1:
            errors.append(f"{k}: area {c[k]['area_m2']} vs {l[k]['area_m2']}")
        errors += [f"{k}/{h}: local only" for h in sorted(l[k]["homes"] - c[k]["homes"])]
        errors += [f"{k}/{h}: cloud only" for h in sorted(c[k]["homes"] - l[k]["homes"])]
    return errors


def summarise(rows: list[dict], zones: list[dict], homes: list[dict]) -> dict:
    bz = _by_zone(rows)
    counts: dict[str, int] = {}
    for z in bz.values():
        for h in z["homes"]:
            counts[h] = counts.get(h, 0) + 1
    shape = {5: "box", 2: "line", 1: "point"}
    table = []
    for z in zones:
        info = bz[z["zone_id"]]
        reversed_corners = z["x1"] > z["x2"] or z["y1"] > z["y2"]
        table.append({"zone_id": z["zone_id"], "zone": z["zone"], "shape": shape[info["npoints"]],
                      "corners_reversed": reversed_corners, "area_km2": round(info["area_m2"] / 1e6, 2),
                      "homes": len(info["homes"]),
                      "also_in_another_zone": sum(1 for h in info["homes"] if counts[h] > 1)})
    bad = [t["zone_id"] for t in table if t["shape"] != "box"]
    reached, dupes = len(counts), sum(1 for v in counts.values() if v > 1)
    flyers = sum(counts.values())
    return {
        "zones": len(zones), "homes": len(homes), "flyers": flyers, "homes_reached": reached,
        "homes_with_two_flyers": dupes, "homes_missed": len(homes) - reached, "bad_zones": bad,
        "reversed_corner_zones": [t["zone_id"] for t in table if t["corners_reversed"]],
        "wasted_usd": round(dupes * COST_PER_FLYER, 2),
        "table": table,
        "headline": f"{flyers} flyers reach {reached} of {len(homes)} homes; {dupes} homes get two (Z3/Z4 overlap); "
                    f"{len(bad)} mistyped zone collapsed to a line and reaches nobody",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        zones, homes = make_data()
        for path, rows in ((ZONES_CSV, zones), (HOMES_CSV, homes)):
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        raise SystemExit(0)
    zones, homes = load()
    if mode == "--sql":
        print(sql_statements(zones, homes)["databricks"] + ";")
    elif mode == "--databricks":
        local = run_local(zones, homes)
        cloud = run_databricks(zones, homes)
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{len(errors)} difference(s): "
                  f"{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud, zones, homes)
        per_zone = {t["zone"]: f"{t['shape']} {t['homes']}" for t in s["table"]}
        print("::notice title=Databricks day 16::st_makeenvelope zones + st_contains ran on a serverless SQL warehouse "
              f"and matched the local box filter: {s['headline']}; per zone {json.dumps(per_zone)}")
    else:
        summary = summarise(run_local(zones, homes), zones, homes)
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB min/max box filter (local) / Databricks SQL "
                                                      "st_makeenvelope + st_contains (cloud)", **summary},
                                                     indent=2) + "\n")
        print(json.dumps(summary, indent=2))
