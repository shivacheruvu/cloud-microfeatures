"""Day 20 (Google Cloud): substitute chains for out-of-stock items with BigQuery Graph. Store staff record "if X is
out, offer Y" as edges. Following those edges a few hops finds an in-stock substitute, but the edges also loop
(oat milk A -> oat milk B -> oat milk A). A property graph over two tables, one quantified GQL pattern
(`-[:SubstitutesFor]->{1,3}`), and the path functions IS_ACYCLIC / IS_TRAIL / PATH_LENGTH (GA 2026-08-31) list every
1-3 hop walk and flag the loops; the nearest in-stock product on an acyclic walk is the substitute to offer.

  python substitutes.py --local             # DuckDB recursive CTE twin of the GQL query; writes result.json
  python substitutes.py --sql setup P.DS    # tables + CREATE PROPERTY GRAPH
  python substitutes.py --sql walks P.DS    # the GQL query (quantified path)
  python substitutes.py --sql fixed1 P.DS   # fallback: fixed-length pattern for 1 / 2 / 3 hops (fixed2, fixed3)
  python substitutes.py --compare FILE...   # check the bq output cloud.sh saved

The cloud check (cloud.sh, GitHub Actions), errors when:
  * the tables or the property graph can't be created, or the graph query fails
  * the acyclic walks BigQuery returns (source, destination, hops, in stock) differ from the local model
  * the substitute picked for any out-of-stock product differs
Walks that revisit a product are reported but only warned about if their count differs (engines may differ on
whether a walk can reuse an edge). Products are made up; the dataset is short-lived and deleted by cloud.sh.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_HOPS = 3

# sku, name, in stock, price (cents)
PRODUCTS = [
    ("O1", "oat milk barista 64oz", False, 549),
    ("O2", "oat milk original 64oz", False, 499),
    ("S1", "soy milk 64oz", False, 399),
    ("A1", "almond milk 64oz", True, 429),
    ("A2", "cashew milk 64oz", True, 599),
    ("Y1", "greek yogurt plain 32oz", False, 549),
    ("Y2", "skyr plain 500g", False, 429),
    ("Y3", "icelandic yogurt 24oz", True, 499),
    ("B1", "sourdough loaf", False, 499),
    ("B2", "rustic white loaf", False, 449),
]
# "if FROM is out, offer TO"
EDGES = [("O1", "O2"), ("O2", "O1"), ("O2", "S1"), ("S1", "A1"), ("S1", "A2"), ("A1", "O1"),
         ("Y1", "Y2"), ("Y2", "Y1"), ("Y2", "Y3"),
         ("B1", "B2"), ("B2", "B1")]

COLUMNS = ["src_sku", "dst_sku", "dst_in_stock", "hops", "acyclic", "trail"]
FIXED_TAILS = {
    1: "(dst:Product)",
    2: "(m1:Product)-[e2:SubstitutesFor]->(dst:Product)",
    3: "(m1:Product)-[e2:SubstitutesFor]->(m2:Product)-[e3:SubstitutesFor]->(dst:Product)",
}


def sql_blocks() -> dict[str, str]:
    out = {}
    for chunk in (HERE / "graph.sql").read_text().split("-- name:")[1:]:
        lines = chunk.strip().splitlines()
        body = "\n".join(l for l in lines[1:] if not l.lstrip().startswith("--")).strip().rstrip(";")
        out[lines[0].strip()] = body
    return out


def bigquery_sql(part: str, target: str = "DATASET") -> str:
    b = sql_blocks()
    if part == "setup":
        products = ", ".join(f"('{s}', '{n}', {str(i).upper()}, {p})" for s, n, i, p in PRODUCTS)
        edges = ", ".join(f"('{a}', '{z}')" for a, z in EDGES)
        sql = b["setup"].replace("{product_rows}", products).replace("{edge_rows}", edges)
    elif part == "walks":
        sql = b["walks"]
    elif part in ("fixed1", "fixed2", "fixed3"):
        sql = b["walks_fixed"].replace("{fixed_tail}", FIXED_TAILS[int(part[-1])])
    else:
        raise ValueError(part)
    return sql.replace("{ds}", target) + ";"


def _norm(rows) -> list[tuple]:
    return sorted((r[0], r[1], bool(r[2]), int(r[3]), bool(r[4]), bool(r[5])) for r in rows)


# ---------- reference model: enumerate walks (edges may repeat, like GQL's default WALK path mode)
def pure_python() -> list[tuple]:
    stock = {s: i for s, _, i, _ in PRODUCTS}
    out = []
    frontier = [[a, z] for a, z in EDGES if not stock[a]]
    while frontier:
        nxt = []
        for path in frontier:
            hops = len(path) - 1
            edges = list(zip(path, path[1:]))
            out.append((path[0], path[-1], stock[path[-1]], hops, len(set(path)) == len(path),
                        len(set(edges)) == len(edges)))
            if hops < MAX_HOPS:
                nxt += [path + [z] for a, z in EDGES if a == path[-1]]
        frontier = nxt
    return sorted(out)


# ---------- local: DuckDB recursive CTE
def run_local() -> list[tuple]:
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE products (sku VARCHAR, name VARCHAR, in_stock BOOLEAN, price_cents BIGINT)")
    con.execute("CREATE TABLE substitutes (from_sku VARCHAR, to_sku VARCHAR)")
    con.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", PRODUCTS)
    con.executemany("INSERT INTO substitutes VALUES (?, ?)", EDGES)
    return _norm(con.execute(sql_blocks()["walks_duckdb"]).fetchall())


# ---------- what the store offers
def pick_substitutes(walks: list[tuple]) -> dict[str, dict]:
    """Per out-of-stock product: the in-stock product on the fewest-hop acyclic walk (ties: closest price, sku)."""
    price = {s: p for s, _, _, p in PRODUCTS}
    best: dict[str, tuple] = {}
    for src, dst, in_stock, hops, acyclic, _ in walks:
        if in_stock and acyclic:
            key = (hops, abs(price[dst] - price[src]), dst)
            if src not in best or key < best[src]:
                best[src] = key
    return {s: ({"offer": best[s][2], "hops": best[s][0]} if s in best else None)
            for s, _, i, _ in PRODUCTS if not i}


def acyclic_set(walks: list[tuple]) -> Counter:
    return Counter((s, d, i, h) for s, d, i, h, a, _ in walks if a)


def compare(cloud: list[tuple], local: list[tuple]) -> tuple[list[str], list[str]]:
    """-> (errors, warnings)."""
    errors, warnings = [], []
    c, l = acyclic_set(cloud), acyclic_set(local)
    if c != l:
        errors.append(f"acyclic walks: only cloud {sorted((c - l).elements())[:4]}, "
                      f"only local {sorted((l - c).elements())[:4]}")
    pc, pl = pick_substitutes(cloud), pick_substitutes(local)
    errors += [f"{s}: cloud offers {pc.get(s)}, local {pl[s]}" for s in pl if pc.get(s) != pl[s]]
    nc, nl = sum(1 for w in cloud if not w[4]), sum(1 for w in local if not w[4])
    if nc != nl:
        warnings.append(f"{nc} looping walks in BigQuery vs {nl} locally")
    return errors, warnings


def summarise(walks: list[tuple]) -> dict:
    names = {s: n for s, n, _, _ in PRODUCTS}
    picks = pick_substitutes(walks)
    loops = sum(1 for w in walks if not w[4])
    no_offer = [s for s, v in picks.items() if v is None]
    return {
        "products": len(PRODUCTS), "out_of_stock": len(picks), "substitution_edges": len(EDGES),
        "walks_1_to_3_hops": len(walks), "acyclic_walks": len(walks) - loops, "looping_walks": loops,
        "walks_reusing_an_edge": sum(1 for w in walks if not w[5]),
        "offers": [{"out_of_stock": names[s], "offer": names[v["offer"]] if v else None,
                    "hops": v["hops"] if v else None} for s, v in picks.items()],
        "headline": f"{len(picks) - len(no_offer)} of {len(picks)} out-of-stock items get an in-stock substitute "
                    f"within {MAX_HOPS} hops; IS_ACYCLIC drops {loops} of {len(walks)} walks that loop back"
                    + (f"; no offer for {', '.join(names[s] for s in no_offer)}" if no_offer else ""),
    }


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
            def b(v):
                return v if isinstance(v, bool) else str(v).lower() == "true"
            return sorted((r["src_sku"], r["dst_sku"], b(r["dst_in_stock"]), int(r["hops"]), b(r["acyclic"]),
                           b(r["trail"])) for r in obj)
    print(f"::error title=Unreadable BigQuery output::{path.name}: {_redact(text[:400])!r}")
    raise SystemExit(1)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(bigquery_sql(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "DATASET"))
    elif mode == "--compare":
        cloud = sorted(r for f in sys.argv[2:] for r in read_rows(Path(f)))
        # the GitHub Actions GCP job has no DuckDB: compare with the plain-Python model, which the local test
        # checks is identical to the DuckDB recursive CTE
        errors, warnings = compare(cloud, pure_python())
        for w in warnings:
            print(f"::warning title=Day 20 looping walks::{w} (acyclic walks and offers still checked)")
        if errors:
            print(f"::error title=Day 20 graph result differs::{_redact(json.dumps(errors))[:850]}")
            raise SystemExit(1)
        s = summarise(cloud)
        offers = {o["out_of_stock"]: o["offer"] for o in s["offers"]}
        print(f"::notice title=Google Cloud day 20::BigQuery Graph quantified path + IS_ACYCLIC matched the local "
              f"model: {s['headline']}; offers {json.dumps(offers)[:450]}")
    else:
        walks = run_local()
        assert walks == pure_python(), "DuckDB and the plain-Python model disagree"
        s = summarise(walks)
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB recursive CTE (local) / BigQuery Graph GQL "
                                                      "with IS_ACYCLIC (cloud)", **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
