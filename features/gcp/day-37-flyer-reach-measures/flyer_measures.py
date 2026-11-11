"""Day 37 (Google Cloud): count each product and each flyer once when rolling weekly units and flyer reach up to brands,
with BigQuery Graph **measures** (GA, release note 2026-11-10): `MEASURE(SUM(...))` properties on node tables, read
through `GRAPH_EXPAND` and `AGG`, which aggregate once per node KEY before rolling up.

  python flyer_measures.py                          # local: DuckDB (dedupe at key grain) == plain Python, writes result.json
  python flyer_measures.py --sql setup DATASET      # tables + CREATE PROPERTY GRAPH with MEASURE properties
  python flyer_measures.py --sql expand DATASET GRAPH_REF   # GRAPH_EXPAND query: naive SUM next to AGG(measure)
  python flyer_measures.py --sql guard DATASET GRAPH_REF    # SUM over a measure: must be refused
  python flyer_measures.py --compare expand.json    # cloud.sh: the BigQuery rows must equal the local model

Graph: FlyerSlots (a product placed on a flyer) -> Products -> Brands, and FlyerSlots -> Flyers. A product on three
flyers appears in three flattened rows, so SUM(Products_weekly_units) counts its units three times; likewise a flyer
carrying three of a brand's products counts its households three times. AGG over the measures counts each once.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

BRANDS = [("B1", "Green Valley Dairy-Free"), ("B2", "Harbor Bakery"), ("B3", "Sunny Fields Produce")]
PRODUCTS = [  # product_id, name, brand_id, weekly units sold (all stores)
    ("P1", "Oat milk 64 oz", "B1", 420), ("P2", "Almond milk 64 oz", "B1", 260), ("P3", "Oat yogurt 24 oz", "B1", 150),
    ("P4", "Sourdough loaf", "B2", 310), ("P5", "Bagels 6 ct", "B2", 180),
    ("P6", "Strawberries 1 lb", "B3", 520), ("P7", "Baby spinach 5 oz", "B3", 240), ("P8", "Honeycrisp apples 3 lb", "B3", 390),
]
FLYERS = [  # flyer_id, name, households reached
    ("F1", "Weekly ad", 48000), ("F2", "Digital coupons", 21000), ("F3", "Plant-based promo", 9000),
    ("F4", "Weekend fresh", 15000),
]
SLOTS = [  # slot_id, flyer_id, product_id (every product is on at least one flyer)
    ("S01", "F1", "P1"), ("S02", "F2", "P1"), ("S03", "F3", "P1"), ("S04", "F3", "P2"), ("S05", "F2", "P3"),
    ("S06", "F3", "P3"), ("S07", "F1", "P4"), ("S08", "F1", "P5"), ("S09", "F4", "P5"), ("S10", "F1", "P6"),
    ("S11", "F2", "P6"), ("S12", "F4", "P6"), ("S13", "F4", "P7"), ("S14", "F4", "P8"),
]
COLS = ("brand", "naive_units", "units", "products", "slots", "naive_households", "households")


# ---------- plain-Python model
def pure_python() -> list[tuple]:
    prod = {p: (b, u) for p, _, b, u in PRODUCTS}
    hh = {f: h for f, _, h in FLYERS}
    names = dict(BRANDS)
    out = []
    for b, _ in BRANDS:
        rows = [(s, f, p) for s, f, p in SLOTS if prod[p][0] == b]   # flattened rows for this brand
        ps, fs = {p for _, _, p in rows}, {f for _, f, _ in rows}
        out.append((names[b], sum(prod[p][1] for _, _, p in rows), sum(prod[p][1] for p in ps), len(ps), len(rows),
                    sum(hh[f] for _, f, _ in rows), sum(hh[f] for f in fs)))
    return sorted(out)


# ---------- DuckDB twin: the flattened join (what GRAPH_EXPAND returns), naive SUMs, and sums at each key's grain
DUCKDB_SQL = """
WITH flat AS (
  SELECT b.brand_name, s.slot_id, p.product_id, p.weekly_units, f.flyer_id, f.households
  FROM slots s JOIN products p USING (product_id) JOIN brands b USING (brand_id) JOIN flyers f USING (flyer_id)
)
SELECT brand_name AS brand,
  SUM(weekly_units) AS naive_units,
  (SELECT SUM(weekly_units) FROM (SELECT DISTINCT product_id, weekly_units FROM flat x WHERE x.brand_name = flat.brand_name)) AS units,
  COUNT(DISTINCT product_id) AS products,
  COUNT(slot_id) AS slots,
  SUM(households) AS naive_households,
  (SELECT SUM(households) FROM (SELECT DISTINCT flyer_id, households FROM flat x WHERE x.brand_name = flat.brand_name)) AS households
FROM flat GROUP BY brand_name ORDER BY brand_name
"""


def run_local() -> list[tuple]:
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE brands (brand_id VARCHAR, brand_name VARCHAR)")
    con.execute("CREATE TABLE products (product_id VARCHAR, product_name VARCHAR, brand_id VARCHAR, weekly_units INT)")
    con.execute("CREATE TABLE flyers (flyer_id VARCHAR, flyer_name VARCHAR, households INT)")
    con.execute("CREATE TABLE slots (slot_id VARCHAR, flyer_id VARCHAR, product_id VARCHAR)")
    con.executemany("INSERT INTO brands VALUES (?, ?)", BRANDS)
    con.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", PRODUCTS)
    con.executemany("INSERT INTO flyers VALUES (?, ?, ?)", FLYERS)
    con.executemany("INSERT INTO slots VALUES (?, ?, ?)", SLOTS)
    return sorted((r[0], *map(int, r[1:])) for r in con.execute(DUCKDB_SQL).fetchall())


# ---------- BigQuery
SETUP_SQL = """
CREATE OR REPLACE TABLE `{ds}`.Brands (brand_id STRING NOT NULL, brand_name STRING, PRIMARY KEY (brand_id) NOT ENFORCED);
CREATE OR REPLACE TABLE `{ds}`.Products (product_id STRING NOT NULL, product_name STRING, brand_id STRING, weekly_units INT64,
  PRIMARY KEY (product_id) NOT ENFORCED);
CREATE OR REPLACE TABLE `{ds}`.Flyers (flyer_id STRING NOT NULL, flyer_name STRING, households INT64,
  PRIMARY KEY (flyer_id) NOT ENFORCED);
CREATE OR REPLACE TABLE `{ds}`.FlyerSlots (slot_id STRING NOT NULL, flyer_id STRING, product_id STRING,
  PRIMARY KEY (slot_id) NOT ENFORCED);
INSERT INTO `{ds}`.Brands VALUES {brand_rows};
INSERT INTO `{ds}`.Products VALUES {product_rows};
INSERT INTO `{ds}`.Flyers VALUES {flyer_rows};
INSERT INTO `{ds}`.FlyerSlots VALUES {slot_rows};
CREATE OR REPLACE PROPERTY GRAPH `{ds}`.FlyerGraph
  NODE TABLES (
    `{ds}`.Brands KEY (brand_id)
      PROPERTIES (brand_id, brand_name),
    `{ds}`.Products KEY (product_id)
      PROPERTIES (product_id, product_name, brand_id, weekly_units,
        MEASURE(SUM(weekly_units)) AS total_units,
        MEASURE(COUNT(product_id)) AS product_count),
    `{ds}`.Flyers KEY (flyer_id)
      PROPERTIES (flyer_id, flyer_name, households,
        MEASURE(SUM(households)) AS total_households),
    `{ds}`.FlyerSlots KEY (slot_id)
      PROPERTIES (slot_id, flyer_id, product_id,
        MEASURE(COUNT(slot_id)) AS slot_count)
  )
  EDGE TABLES (
    `{ds}`.FlyerSlots AS SlotToProduct
      SOURCE KEY (slot_id) REFERENCES FlyerSlots (slot_id)
      DESTINATION KEY (product_id) REFERENCES Products (product_id) NO PROPERTIES,
    `{ds}`.FlyerSlots AS SlotToFlyer
      SOURCE KEY (slot_id) REFERENCES FlyerSlots (slot_id)
      DESTINATION KEY (flyer_id) REFERENCES Flyers (flyer_id) NO PROPERTIES,
    `{ds}`.Products AS ProductToBrand
      SOURCE KEY (product_id) REFERENCES Products (product_id)
      DESTINATION KEY (brand_id) REFERENCES Brands (brand_id) NO PROPERTIES
  );
"""
# Raw columns flatten to <NodeTable>_<property>; measure columns can only be read through AGG.
EXPAND_SQL = """
SELECT
  Brands_brand_name AS brand,
  SUM(Products_weekly_units) AS naive_units,
  AGG(Products_total_units) AS units,
  AGG(Products_product_count) AS products,
  AGG(FlyerSlots_slot_count) AS slots,
  SUM(Flyers_households) AS naive_households,
  AGG(Flyers_total_households) AS households
FROM GRAPH_EXPAND("{ref}")
GROUP BY brand
ORDER BY brand;
"""
GUARD_SQL = """
SELECT Brands_brand_name, SUM(Products_total_units) AS units
FROM GRAPH_EXPAND("{ref}")
GROUP BY 1;
"""


def _q(v) -> str:
    return str(v) if isinstance(v, int) else "'" + str(v).replace("'", "\\'") + "'"


def _rows(rows) -> str:
    return ", ".join("(" + ", ".join(_q(v) for v in r) + ")" for r in rows)


def bigquery_sql(part: str, ds: str, ref: str | None = None) -> str:
    ref = ref or f"{ds}.FlyerGraph"
    if part == "setup":
        return SETUP_SQL.format(ds=ds, brand_rows=_rows(BRANDS), product_rows=_rows(PRODUCTS),
                                flyer_rows=_rows(FLYERS), slot_rows=_rows(SLOTS))
    if part == "expand":
        return EXPAND_SQL.format(ref=ref)
    if part == "guard":
        return GUARD_SQL.format(ref=ref)
    raise ValueError(part)


# ---------- summary
def summarise(rows: list[tuple]) -> dict:
    d = [dict(zip(COLS, r)) for r in rows]
    naive, true = sum(r["naive_units"] for r in d), sum(r["units"] for r in d)
    worst = max(d, key=lambda r: r["naive_units"] / r["units"])
    return {
        "brands": len(BRANDS), "products": len(PRODUCTS), "flyers": len(FLYERS), "flyer_slots": len(SLOTS),
        "by_brand": d,
        "units_true": true, "units_naive": naive, "units_overcount_pct": round(100 * (naive - true) / true, 1),
        "worst_brand": worst["brand"], "worst_brand_factor": round(worst["naive_units"] / worst["units"], 2),
        "headline": f"AGG over graph measures: {true} weekly units across {len(BRANDS)} brands; naive SUM over the "
                    f"flattened graph says {naive} (+{round(100 * (naive - true) / true, 1)}%), "
                    f"{worst['brand']} inflated {round(worst['naive_units'] / worst['units'], 2)}x",
    }


def compare(cloud: list[tuple], local: list[tuple]) -> list[str]:
    if cloud == local:
        return []
    c, l = set(cloud), set(local)
    return [f"only cloud {sorted(c - l)[:3]}", f"only local {sorted(l - c)[:3]}"]


# ---------- cloud output
def _redact(text: str) -> str:
    return re.sub(r"\d{6,}", "<number>", text.replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>"))


def read_rows(path: Path) -> list[tuple]:
    """bq --format=json output (may start with a warning line) -> sorted tuples in COLS order."""
    text = path.read_text()
    dec = json.JSONDecoder()
    for i in (k for k, ch in enumerate(text) if ch == "["):
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, list) and obj and all(isinstance(r, dict) for r in obj):
            return sorted((r["brand"], *(int(float(r[c])) for c in COLS[1:])) for r in obj)
    print(f"::error title=Unreadable BigQuery output::{path.name}: {_redact(text[:400])!r}")
    raise SystemExit(1)


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "--local"
    if mode == "--sql":
        print(bigquery_sql(argv[1], argv[2] if len(argv) > 2 else "DATASET", argv[3] if len(argv) > 3 else None))
        return 0
    if mode == "--compare":
        cloud = read_rows(Path(argv[1]))
        errors = compare(cloud, pure_python())   # the GCP job has no DuckDB; the local test ties DuckDB to this model
        if errors:
            print(f"::error title=Day 37 graph measures differ::{_redact(json.dumps(errors))[:850]}")
            return 1
        s = summarise(cloud)
        per = "; ".join(f"{r['brand']}: AGG {r['units']} vs SUM {r['naive_units']} units, households AGG "
                        f"{r['households']} vs SUM {r['naive_households']}" for r in s["by_brand"])
        print(f"::notice title=Google Cloud day 37::GRAPH_EXPAND + AGG over graph measures matched the local model "
              f"({len(cloud)} brands): {s['headline']}. {per}")
        return 0
    rows = run_local()
    assert rows == pure_python(), "DuckDB and the plain-Python model disagree"
    s = summarise(rows)
    (HERE / "result.json").write_text(json.dumps(
        {"engine": "DuckDB key-grain dedupe + plain Python (local) / BigQuery Graph measures via GRAPH_EXPAND + AGG "
                   "(cloud)", **s}, indent=2) + "\n")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
