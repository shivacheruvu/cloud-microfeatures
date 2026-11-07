"""Day 33 (Google Cloud): route oat-milk stock transfers between stores with BigQuery Graph `ANY SHORTEST`, now that
BigQuery Graph / GQL queries run on on-demand pricing (release note 2026-11-06; before that they needed an Enterprise
reservation, which is why day 20 could only be verified locally).

  python transfer_routes.py                       # local: DuckDB recursive CTE == plain-Python BFS, writes result.json
  python transfer_routes.py --sql setup|routes DATASET   # BigQuery SQL / GQL for cloud.sh
  python transfer_routes.py --compare routes.json         # cloud.sh: check the GQL rows equal the local model

Graph: Store nodes (oat-milk cases over or under target) and Road edges (both directions, drive minutes). For every
surplus store -> short store pair, `ANY SHORTEST` returns one fewest-hop path of 1-3 hops; `LET minutes = SUM(e.minutes)`
adds up its drive time. Each short store is then served from the source with the fewest hops (ties: fewer minutes,
then store id). The road network is built so every fewest-hop path is unique, so ANY SHORTEST's pick is deterministic.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_HOPS = 3

STORES = [  # store_id, name, surplus cases, short cases (oat milk 64 oz, vs. this week's target)
    ("DC", "distribution center", 60, 0), ("S1", "Elm St", 0, 0), ("S2", "Riverside", 24, 0),
    ("S3", "Oak Park", 0, 6), ("S4", "Mill Rd", 0, 0), ("S5", "Hillcrest", 0, 10),
    ("S6", "Lakeview", 0, 4), ("S7", "North Gate", 0, 5), ("S8", "Airport", 0, 3),
]
ROADS = [  # undirected: store, store, drive minutes
    ("DC", "S1", 10), ("DC", "S2", 18), ("S1", "S3", 12), ("S2", "S4", 9), ("S3", "S4", 15),
    ("S4", "S6", 11), ("S6", "S7", 13), ("S7", "S8", 16), ("S4", "S5", 7),
]


def edges() -> list[tuple[str, str, int]]:
    return sorted([(a, b, m) for a, b, m in ROADS] + [(b, a, m) for a, b, m in ROADS])


def sources() -> list[str]:
    return [s for s, _, sur, _ in STORES if sur > 0]


def shorts() -> list[str]:
    return [s for s, _, _, sh in STORES if sh > 0]


# ---------- plain-Python model: BFS by hops, then every fewest-hop path (must be one) and its minutes
def _min_hop_paths(src: str, dst: str) -> list[list[str]]:
    adj: dict[str, list[str]] = {}
    for a, b, _ in edges():
        adj.setdefault(a, []).append(b)
    paths, q = [], deque([[src]])
    while q:
        p = q.popleft()
        if paths and len(p) > len(paths[0]):
            break
        if len(p) > 1 and p[-1] == dst:
            paths.append(p)
            continue
        if len(p) - 1 < MAX_HOPS:
            q.extend(p + [n] for n in adj[p[-1]] if n not in p)
    return paths


def pure_python() -> list[tuple]:
    """-> sorted (src, dst, hops, minutes) for every surplus -> short pair reachable within MAX_HOPS."""
    mins = {(a, b): m for a, b, m in edges()}
    rows = []
    for s in sources():
        for d in shorts():
            ps = _min_hop_paths(s, d)
            if not ps:
                continue
            if len({tuple(p) for p in ps}) != 1:
                raise ValueError(f"{s}->{d} has {len(ps)} fewest-hop paths; ANY SHORTEST would be ambiguous")
            p = ps[0]
            rows.append((s, d, len(p) - 1, sum(mins[(a, b)] for a, b in zip(p, p[1:]))))
    return sorted(rows)


DUCKDB_ROUTES = """
WITH RECURSIVE w(src, dst, hops, minutes, nodes) AS (
  SELECT r.from_store, r.to_store, 1, r.minutes, [r.from_store, r.to_store]
  FROM roads r JOIN stores s ON s.store_id = r.from_store WHERE s.surplus_cases > 0
  UNION ALL
  SELECT w.src, r.to_store, w.hops + 1, w.minutes + r.minutes, list_append(w.nodes, r.to_store)
  FROM w JOIN roads r ON r.from_store = w.dst
  WHERE w.hops < 3 AND NOT list_contains(w.nodes, r.to_store)
)
SELECT src, dst, hops, minutes FROM (
  SELECT w.*, MIN(hops) OVER (PARTITION BY src, dst) AS best
  FROM w JOIN stores d ON d.store_id = w.dst WHERE d.short_cases > 0
) WHERE hops = best ORDER BY src, dst
"""


def run_local() -> list[tuple]:
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE stores (store_id VARCHAR, name VARCHAR, surplus_cases INT, short_cases INT)")
    con.execute("CREATE TABLE roads (from_store VARCHAR, to_store VARCHAR, minutes INT)")
    con.executemany("INSERT INTO stores VALUES (?, ?, ?, ?)", STORES)
    con.executemany("INSERT INTO roads VALUES (?, ?, ?)", edges())
    return sorted((s, d, int(h), int(m)) for s, d, h, m in con.execute(DUCKDB_ROUTES).fetchall())


# ---------- BigQuery
SETUP_SQL = """
CREATE OR REPLACE TABLE `{ds}`.stores (
  store_id STRING NOT NULL, name STRING, surplus_cases INT64, short_cases INT64,
  PRIMARY KEY (store_id) NOT ENFORCED
);
CREATE OR REPLACE TABLE `{ds}`.roads (
  from_store STRING NOT NULL, to_store STRING NOT NULL, minutes INT64,
  PRIMARY KEY (from_store, to_store) NOT ENFORCED
);
INSERT INTO `{ds}`.stores VALUES {store_rows};
INSERT INTO `{ds}`.roads VALUES {road_rows};
CREATE OR REPLACE PROPERTY GRAPH `{ds}`.store_routes
  NODE TABLES (
    `{ds}`.stores KEY (store_id) LABEL Store
  )
  EDGE TABLES (
    `{ds}`.roads KEY (from_store, to_store)
      SOURCE KEY (from_store) REFERENCES stores (store_id)
      DESTINATION KEY (to_store) REFERENCES stores (store_id)
      LABEL Road
  );
"""
# ANY SHORTEST: one fewest-hop path per (src, dst) pair; e is a group variable over the path's edges.
ROUTES_GQL = """
GRAPH `{ds}`.store_routes
MATCH ANY SHORTEST (src:Store WHERE src.surplus_cases > 0)-[e:Road]->{{1, 3}}(dst:Store WHERE dst.short_cases > 0)
LET minutes = SUM(e.minutes)
RETURN src.store_id AS src, dst.store_id AS dst, ARRAY_LENGTH(e) AS hops, minutes
ORDER BY src, dst;
"""


def bigquery_sql(part: str, ds: str) -> str:
    if part == "setup":
        return SETUP_SQL.format(
            ds=ds,
            store_rows=", ".join(f"('{s}', '{n}', {a}, {b})" for s, n, a, b in STORES),
            road_rows=", ".join(f"('{a}', '{b}', {m})" for a, b, m in edges()))
    if part == "routes":
        return ROUTES_GQL.format(ds=ds)
    raise ValueError(part)


# ---------- the transfer plan
def plan(routes: list[tuple]) -> dict[str, dict | None]:
    """Per short store: the source with the fewest hops (ties: fewer minutes, then store id)."""
    best: dict[str, tuple] = {}
    for s, d, h, m in routes:
        key = (h, m, s)
        if d not in best or key < best[d]:
            best[d] = key
    return {d: ({"from": best[d][2], "hops": best[d][0], "minutes": best[d][1]} if d in best else None)
            for d in shorts()}


def summarise(routes: list[tuple]) -> dict:
    p = plan(routes)
    short = {s: sh for s, _, _, sh in STORES}
    unserved = [d for d, v in p.items() if v is None]
    served = {d: v for d, v in p.items() if v}
    return {
        "stores": len(STORES), "roads": len(ROADS), "surplus_stores": len(sources()), "short_stores": len(shorts()),
        "pairs_within_3_hops": len(routes),
        "routes": [{"src": s, "dst": d, "hops": h, "minutes": m} for s, d, h, m in routes],
        "plan": p,
        "cases_routed": sum(short[d] for d in served), "drive_minutes": sum(v["minutes"] for v in served.values()),
        "unserved": unserved,
        "headline": f"{len(served)} of {len(p)} short stores get a transfer route within {MAX_HOPS} hops "
                    f"({sum(short[d] for d in served)} cases, {sum(v['minutes'] for v in served.values())} drive "
                    f"minutes)" + (f"; no source within reach of {', '.join(unserved)}" if unserved else ""),
    }


def compare(cloud: list[tuple], local: list[tuple]) -> list[str]:
    errors = []
    if cloud != local:
        c, l = set(cloud), set(local)
        errors.append(f"routes: only cloud {sorted(c - l)[:4]}, only local {sorted(l - c)[:4]}")
    pc, pl = plan(cloud), plan(local)
    errors += [f"{d}: cloud plan {pc.get(d)}, local {pl[d]}" for d in pl if pc.get(d) != pl[d]]
    return errors


# ---------- cloud output
def _redact(text: str) -> str:
    return re.sub(r"\d{6,}", "<number>", text.replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>"))


def read_rows(path: Path) -> list[tuple]:
    """bq --format=json output (may start with a warning line) -> sorted (src, dst, hops, minutes)."""
    text = path.read_text()
    dec = json.JSONDecoder()
    for i in (k for k, ch in enumerate(text) if ch == "["):
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, list) and all(isinstance(r, dict) for r in obj):
            return sorted((r["src"], r["dst"], int(r["hops"]), int(r["minutes"])) for r in obj)
    print(f"::error title=Unreadable BigQuery output::{path.name}: {_redact(text[:400])!r}")
    raise SystemExit(1)


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "--local"
    if mode == "--sql":
        print(bigquery_sql(argv[1], argv[2] if len(argv) > 2 else "DATASET"))
        return 0
    if mode == "--compare":
        cloud = read_rows(Path(argv[1]))
        # the GitHub Actions GCP job has no DuckDB: compare with the plain-Python model, which the local test checks
        # is identical to the DuckDB recursive CTE
        errors = compare(cloud, pure_python())
        if errors:
            print(f"::error title=Day 33 graph result differs::{_redact(json.dumps(errors))[:850]}")
            return 1
        s = summarise(cloud)
        legs = {d: f"{v['from']} {v['hops']} hops {v['minutes']} min" for d, v in s["plan"].items() if v}
        print(f"::notice title=Google Cloud day 33::BigQuery Graph ANY SHORTEST on on-demand pricing matched the "
              f"local model ({len(cloud)} surplus-to-short routes): {s['headline']}; plan {json.dumps(legs)[:400]}")
        return 0
    routes = run_local()
    assert routes == pure_python(), "DuckDB and the plain-Python model disagree"
    s = summarise(routes)
    (HERE / "result.json").write_text(json.dumps(
        {"engine": "DuckDB recursive CTE + plain-Python BFS (local) / BigQuery Graph GQL ANY SHORTEST on on-demand "
                   "pricing (cloud)", **s}, indent=2) + "\n")
    print(json.dumps({k: s[k] for k in ("pairs_within_3_hops", "plan", "headline")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
