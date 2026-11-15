"""Day 41 (Google Cloud): a self-describing aisle graph. Properties and measures carry OPTIONS(description=...), and
`CALL BQ.SHOW_GRAPH_EXPAND_SCHEMA(graph, var)` (release note 2026-11-10, with graph measures GA) reads back the exact
column list GRAPH_EXPAND will return: names, which ones are measures, and their descriptions. The run checks that
schema against a contract computed locally from the same graph definition, then queries the columns it promised.

  python aisle_schema.py                               # local: DuckDB rollup == plain Python, writes result.json
  python aisle_schema.py --sql setup DATASET           # tables + CREATE PROPERTY GRAPH (setup-plain: without OPTIONS)
  python aisle_schema.py --sql schema DATASET GRAPH_REF  # DECLARE + CALL BQ.SHOW_GRAPH_EXPAND_SCHEMA + SELECT
  python aisle_schema.py --sql rollup DATASET GRAPH_REF  # GRAPH_EXPAND + AGG per aisle
  python aisle_schema.py --check-schema schema.json    # cloud.sh: reported schema vs the local contract
  python aisle_schema.py --compare rollup.json         # cloud.sh: rollup rows vs the local model

Graph: ShelfScans (a restock scan of a product) -> Products -> Aisles. ShelfScans is the single root node table.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

AISLES = [("A1", "Dairy"), ("A2", "Bakery"), ("A3", "Produce")]
PRODUCTS = [  # product_id, name, aisle_id, shelf facings
    ("P1", "Whole milk 1 gal", "A1", 6), ("P2", "Greek yogurt 32 oz", "A1", 4), ("P3", "Salted butter 1 lb", "A1", 3),
    ("P4", "Sourdough loaf", "A2", 5), ("P5", "Bagels 6 ct", "A2", 2),
    ("P6", "Bananas per lb", "A3", 8), ("P7", "Baby spinach 5 oz", "A3", 3),
]
SCANS = [  # scan_id, product_id, units restocked
    ("S01", "P1", 24), ("S02", "P1", 18), ("S03", "P1", 12), ("S04", "P2", 10), ("S05", "P3", 8),
    ("S06", "P4", 15), ("S07", "P4", 9), ("S08", "P5", 12),
    ("S09", "P6", 40), ("S10", "P6", 35), ("S11", "P6", 30), ("S12", "P7", 14),
]

# The graph definition both the DDL and the expected GRAPH_EXPAND schema come from.
# (property, description or None, measure expression or None)
GRAPH = {
    "Aisles": ("aisle_id", [
        ("aisle_id", None, None),
        ("aisle_name", "Store aisle as shown on the shelf sign", None),
    ]),
    "Products": ("product_id", [
        ("product_id", None, None),
        ("product_name", "Shelf label name of the product", None),
        ("aisle_id", None, None),
        ("facings", "Number of facings the planogram gives the product", None),
        ("total_facings", "Shelf facings, counted once per product", "SUM(facings)"),
    ]),
    "ShelfScans": ("scan_id", [
        ("scan_id", None, None),
        ("product_id", None, None),
        ("units", "Units put on the shelf in one restock scan", None),
        ("total_units", "Units restocked, counted once per scan", "SUM(units)"),
        ("scan_count", "Number of restock scans", "COUNT(scan_id)"),
    ]),
}
EDGES = [("ScanToProduct", "ShelfScans", "scan_id", "product_id", "Products"),
         ("ProductToAisle", "Products", "product_id", "aisle_id", "Aisles")]
COLS = ("aisle", "products", "scans", "naive_facings", "facings", "units")


def expected_schema() -> dict:
    """{column: {"measure": bool, "description": str|None}} that GRAPH_EXPAND should return (<NodeTable>_<property>)."""
    return {f"{t}_{p}": {"measure": m is not None, "description": d}
            for t, (_, props) in GRAPH.items() for p, d, m in props}


# ---------- plain-Python model
def pure_python() -> list[tuple]:
    prod = {p: (a, f) for p, _, a, f in PRODUCTS}
    out = []
    for a, name in AISLES:
        rows = [(s, p, u) for s, p, u in SCANS if prod[p][0] == a]       # flattened rows (one per scan)
        ps = {p for _, p, _ in rows}
        out.append((name, len(ps), len(rows), sum(prod[p][1] for _, p, _ in rows),
                    sum(prod[p][1] for p in ps), sum(u for _, _, u in rows)))
    return sorted(out)


DUCKDB_SQL = """
WITH flat AS (
  SELECT a.aisle_name, s.scan_id, s.units, p.product_id, p.facings
  FROM shelfscans s JOIN products p USING (product_id) JOIN aisles a USING (aisle_id)
)
SELECT aisle_name, COUNT(DISTINCT product_id), COUNT(scan_id), SUM(facings),
  (SELECT SUM(facings) FROM (SELECT DISTINCT product_id, facings FROM flat x WHERE x.aisle_name = flat.aisle_name)),
  SUM(units)
FROM flat GROUP BY aisle_name ORDER BY aisle_name
"""


def run_local() -> list[tuple]:
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE aisles (aisle_id VARCHAR, aisle_name VARCHAR)")
    con.execute("CREATE TABLE products (product_id VARCHAR, product_name VARCHAR, aisle_id VARCHAR, facings INT)")
    con.execute("CREATE TABLE shelfscans (scan_id VARCHAR, product_id VARCHAR, units INT)")
    con.executemany("INSERT INTO aisles VALUES (?, ?)", AISLES)
    con.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", PRODUCTS)
    con.executemany("INSERT INTO shelfscans VALUES (?, ?, ?)", SCANS)
    return sorted((r[0], *map(int, r[1:])) for r in con.execute(DUCKDB_SQL).fetchall())


# ---------- BigQuery
TABLES_SQL = """
CREATE OR REPLACE TABLE `{ds}`.Aisles (aisle_id STRING NOT NULL, aisle_name STRING, PRIMARY KEY (aisle_id) NOT ENFORCED);
CREATE OR REPLACE TABLE `{ds}`.Products (product_id STRING NOT NULL, product_name STRING, aisle_id STRING, facings INT64,
  PRIMARY KEY (product_id) NOT ENFORCED);
CREATE OR REPLACE TABLE `{ds}`.ShelfScans (scan_id STRING NOT NULL, product_id STRING, units INT64,
  PRIMARY KEY (scan_id) NOT ENFORCED);
INSERT INTO `{ds}`.Aisles VALUES {aisle_rows};
INSERT INTO `{ds}`.Products VALUES {product_rows};
INSERT INTO `{ds}`.ShelfScans VALUES {scan_rows};
"""
SCHEMA_SQL = """
DECLARE graph_schema STRING DEFAULT '';
CALL BQ.SHOW_GRAPH_EXPAND_SCHEMA('{ref}', graph_schema);
SELECT graph_schema;
"""
ROLLUP_SQL = """
SELECT
  Aisles_aisle_name AS aisle,
  COUNT(DISTINCT Products_product_id) AS products,
  AGG(ShelfScans_scan_count) AS scans,
  SUM(Products_facings) AS naive_facings,
  AGG(Products_total_facings) AS facings,
  AGG(ShelfScans_total_units) AS units
FROM GRAPH_EXPAND("{ref}")
GROUP BY aisle
ORDER BY aisle;
"""


def _q(v) -> str:
    return str(v) if isinstance(v, int) else "'" + str(v).replace("'", "\\'") + "'"


def _rows(rows) -> str:
    return ", ".join("(" + ", ".join(_q(v) for v in r) + ")" for r in rows)


def graph_ddl(ds: str, plain: bool = False) -> str:
    def prop(p, d, m):
        expr = f"MEASURE({m}) AS {p}" if m else p
        return expr if plain or not d else f'{expr} OPTIONS(description="{d}")'
    nodes = ",\n    ".join(f"`{ds}`.{t} KEY ({k})\n      PROPERTIES ({', '.join(prop(*x) for x in props)})"
                           for t, (k, props) in GRAPH.items())
    edges = ",\n    ".join(f"`{ds}`.{src} AS {name}\n      SOURCE KEY ({sk}) REFERENCES {src} ({sk})\n"
                           f"      DESTINATION KEY ({dk}) REFERENCES {dst} ({dk}) NO PROPERTIES"
                           for name, src, sk, dk, dst in EDGES)
    return f"CREATE OR REPLACE PROPERTY GRAPH `{ds}`.AisleGraph\n  NODE TABLES (\n    {nodes}\n  )\n" \
           f"  EDGE TABLES (\n    {edges}\n  );\n"


def bigquery_sql(part: str, ds: str, ref: str | None = None) -> str:
    ref = ref or f"{ds}.AisleGraph"
    if part in ("setup", "setup-plain"):
        return TABLES_SQL.format(ds=ds, aisle_rows=_rows(AISLES), product_rows=_rows(PRODUCTS),
                                 scan_rows=_rows(SCANS)) + graph_ddl(ds, plain=part == "setup-plain")
    if part == "schema":
        return SCHEMA_SQL.format(ref=ref)
    if part == "rollup":
        return ROLLUP_SQL.format(ref=ref)
    raise ValueError(part)


# ---------- reading the procedure's JSON (format checked loosely: a list of column objects with a name)
def _descr(field: dict) -> str | None:
    for k, v in field.items():
        if "description" in k.lower() and isinstance(v, str) and v:
            return v
        if isinstance(v, dict) and (d := _descr(v)):
            return d
        if isinstance(v, list):
            for x in v:   # e.g. [{"name": "description", "value": "..."}] or [["description", "..."]]
                if isinstance(x, dict) and str(x.get("name", x.get("key", ""))).lower() == "description":
                    return str(x.get("value", ""))
                if isinstance(x, (list, tuple)) and len(x) == 2 and str(x[0]).lower() == "description":
                    return str(x[1])
    return None


def parse_schema(obj) -> dict:
    """Procedure JSON -> {column: {"measure": bool, "description": str|None}}."""
    if isinstance(obj, str):
        obj = json.loads(obj)
    cols: dict = {}

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("name"), str) and any(k in o for k in ("type", "mode", "is_measure")):
                cols[o["name"]] = {"measure": bool(o.get("is_measure")), "description": _descr(o)}
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return cols


def check_schema(reported: dict, expected: dict | None = None) -> dict:
    expected = expected or expected_schema()
    low = {k.lower(): k for k in reported}
    missing = sorted(c for c in expected if c.lower() not in low)
    extra = sorted(k for k in reported if k.lower() not in {c.lower() for c in expected})
    got = {c: reported[low[c.lower()]] for c in expected if c.lower() in low}
    measure_wrong = sorted(c for c, v in got.items() if v["measure"] != expected[c]["measure"])
    want_d = {c for c, v in expected.items() if v["description"]}
    desc_found = sorted(c for c in want_d if c in got and got[c]["description"])
    desc_wrong = sorted(c for c in desc_found if got[c]["description"] != expected[c]["description"])
    return {"expected": len(expected), "reported": len(reported), "missing": missing, "extra": extra,
            "measure_wrong": measure_wrong, "measures": sorted(c for c, v in got.items() if v["measure"]),
            "descriptions_expected": len(want_d), "descriptions_found": len(desc_found), "description_wrong": desc_wrong,
            "ok": not missing and not measure_wrong and not desc_wrong}


# ---------- summary
def summarise(rows: list[tuple]) -> dict:
    d = [dict(zip(COLS, r)) for r in rows]
    exp = expected_schema()
    naive, true = sum(r["naive_facings"] for r in d), sum(r["facings"] for r in d)
    return {
        "aisles": len(AISLES), "products": len(PRODUCTS), "shelf_scans": len(SCANS),
        "graph_expand_columns": len(exp), "measure_columns": sum(v["measure"] for v in exp.values()),
        "described_columns": sum(bool(v["description"]) for v in exp.values()),
        "by_aisle": d, "facings_true": true, "facings_naive": naive, "units": sum(r["units"] for r in d),
        "headline": f"GRAPH_EXPAND schema contract: {len(exp)} columns ({sum(v['measure'] for v in exp.values())} "
                    f"measures, {sum(bool(v['description']) for v in exp.values())} described); {true} shelf "
                    f"facings by AGG, not the {naive} a SUM over restock scans gives",
    }


# ---------- cloud output
def _redact(text: str) -> str:
    return re.sub(r"\d{6,}", "<number>", text.replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>"))


def _json_rows(path: Path) -> list[dict]:
    """bq --format=json output (may start with a warning line, a script may print several result sets): last list."""
    text, dec, found = path.read_text(), json.JSONDecoder(), None
    i = 0
    while (i := text.find("[", i)) != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, list) and obj and all(isinstance(r, dict) for r in obj):
            found = obj
        i = end
    if found is None:
        print(f"::error title=Unreadable BigQuery output::{path.name}: {_redact(text[:400])!r}")
        raise SystemExit(1)
    return found


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "--local"
    if mode == "--sql":
        print(bigquery_sql(argv[1], argv[2] if len(argv) > 2 else "DATASET", argv[3] if len(argv) > 3 else None))
        return 0
    if mode == "--check-schema":
        row = _json_rows(Path(argv[1]))[-1]
        raw = row.get("graph_schema", next(iter(row.values())))
        try:
            reported = parse_schema(raw)
        except (ValueError, TypeError):
            print(f"::error title=Day 41 schema not JSON::{_redact(str(raw)[:600])}")
            return 1
        c = check_schema(reported)
        if not c["ok"]:
            print(f"::error title=Day 41 GRAPH_EXPAND schema differs from the contract::{_redact(json.dumps(c))[:850]}"
                  f" | raw: {_redact(str(raw))[:300]}")
            return 1
        print(f"::notice title=Google Cloud day 41 schema::BQ.SHOW_GRAPH_EXPAND_SCHEMA reported {c['reported']} columns; "
              f"all {c['expected']} contract columns present, measures flagged exactly ({', '.join(c['measures'])}), "
              f"descriptions read back {c['descriptions_found']}/{c['descriptions_expected']}"
              + (f"; extra columns: {', '.join(c['extra'])}" if c["extra"] else "; no extra columns"))
        if c["descriptions_found"] < c["descriptions_expected"]:
            print(f"::warning title=Day 41 descriptions::only {c['descriptions_found']} of {c['descriptions_expected']} "
                  f"OPTIONS descriptions appear in the schema JSON | raw: {_redact(str(raw))[:500]}")
        return 0
    if mode == "--compare":
        cloud = sorted((r["aisle"], *(int(float(r[c])) for c in COLS[1:])) for r in _json_rows(Path(argv[1])))
        if cloud != pure_python():   # the GCP job has no DuckDB; the local test ties DuckDB to this model
            print(f"::error title=Day 41 rollup differs::cloud {cloud} vs local {pure_python()}")
            return 1
        s = summarise(cloud)
        per = "; ".join(f"{r['aisle']}: {r['facings']} facings (SUM says {r['naive_facings']}), {r['units']} units in "
                        f"{r['scans']} scans" for r in s["by_aisle"])
        print(f"::notice title=Google Cloud day 41 rollup::GRAPH_EXPAND + AGG on the promised columns matched the local "
              f"model: {per}")
        return 0
    rows = run_local()
    assert rows == pure_python(), "DuckDB and the plain-Python model disagree"
    s = summarise(rows)
    (HERE / "result.json").write_text(json.dumps(
        {"engine": "DuckDB + plain Python (local) / BQ.SHOW_GRAPH_EXPAND_SCHEMA + GRAPH_EXPAND + AGG (cloud)", **s},
        indent=2) + "\n")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
