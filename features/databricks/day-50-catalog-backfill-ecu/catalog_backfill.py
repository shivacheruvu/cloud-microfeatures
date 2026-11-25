"""Day 50 (Databricks): backfill two new shelf-tag columns on a grocery catalog table with **efficient column updates**
(ECU, Beta, Databricks platform release notes, November 2026, 2026-11-13). With ECU on, an UPDATE that fills a new
column can write the new values to separate column files instead of rewriting every data file with its unchanged
columns (here: the long ingredient text).

The job: a 24-product Thanksgiving catalog (sku, name, ingredients, price_cents) loaded in two batches. Two new columns
are added and filled with one UPDATE:

    ingredient_count INT     = number of comma-separated ingredients
    nut_warning      BOOLEAN = the ingredients mention almond, cashew, hazelnut, peanut, pecan or walnut

The same steps run on two tables in a scratch schema: `catalog_ecu` (created with 'delta.enableColumnUpdates-preview'
= 'true' plus the features it needs) and `catalog_std` (a plain managed table, the control). Then ECU is rolled back on
`catalog_ecu` (property false, REORG TABLE ... APPLY (PURGE)) and the values are read again.

Must hold (else the cloud run fails): both tables' backfilled values equal the local reference (pure Python, and DuckDB
running the same ALTER / UPDATE), before and after the rollback. Reported: the UPDATE's DESCRIBE HISTORY metrics for
both tables (files removed / added, bytes added), so the run shows whether ECU took effect or Databricks fell back to
a standard rewrite (the docs allow the fallback; it is a warning, not a failure). A refused workspace, or the property
being rejected because the Beta is off, is a warning.

  python catalog_backfill.py                # local: pure-Python reference + DuckDB; writes result.json
  python catalog_backfill.py --sql          # print the Databricks statements (catalog <catalog>)
  python catalog_backfill.py --databricks   # serverless SQL warehouse via the Statement Execution API (cloud.sh)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "cmf_day50_ecu"
NUTS = ["almond", "cashew", "hazelnut", "peanut", "pecan", "walnut"]
# sku, name, ingredients (comma separated), price in cents; batch 1 = first 12, batch 2 = last 12
PRODUCTS = [
    ("TKY-12", "Whole young turkey 12 lb", "turkey, water, salt, natural flavor", 2388),
    ("CRN-14", "Jellied cranberry sauce", "cranberries, high fructose corn syrup, corn syrup, water", 229),
    ("STF-170", "Classic stuffing mix", "enriched wheat flour, salt, onion, celery, sage, yeast, butter", 349),
    ("PIE-PMK", "Pumpkin pie", "pumpkin, sugar, eggs, milk, wheat flour, butter, cinnamon, ginger, nutmeg, cloves", 899),
    ("PIE-PCN", "Pecan pie", "pecans, corn syrup, sugar, eggs, butter, wheat flour, vanilla, salt", 1099),
    ("GRV-JAR", "Turkey gravy jar", "turkey stock, wheat flour, turkey fat, salt, onion powder", 279),
    ("RLS-12", "Dinner rolls 12 count", "wheat flour, water, sugar, yeast, butter, salt", 379),
    ("BTR-4", "Unsalted butter 4 sticks", "cream", 549),
    ("SWP-5", "Sweet potatoes 5 lb", "sweet potatoes", 499),
    ("GBN-CAS", "Green bean casserole kit", "green beans, cream of mushroom soup, fried onions, almonds", 649),
    ("MRS-10", "Mini marshmallows", "corn syrup, sugar, modified corn starch, water, gelatin, natural flavor", 199),
    ("WLN-HLV", "Walnut halves", "walnuts", 699),
    ("CRM-PNT", "Heavy cream 1 pint", "cream, carrageenan", 399),
    ("APC-GAL", "Fresh apple cider", "apple cider", 599),
    ("TRL-MIX", "Holiday trail mix", "peanuts, raisins, cashews, chocolate candies, almonds, sunflower seeds", 799),
    ("PTO-RST", "Russet potatoes 5 lb", "potatoes", 399),
    ("CBR-SLD", "Cranberry walnut salad kit", "romaine, dried cranberries, candied walnuts, feta, vinaigrette", 449),
    ("HZL-SPR", "Chocolate hazelnut spread", "sugar, palm oil, hazelnuts, cocoa, skim milk, lecithin, vanillin", 529),
    ("EGG-12", "Large brown eggs", "eggs", 459),
    ("PNB-JAR", "Creamy peanut butter", "roasted peanuts, sugar, hydrogenated vegetable oil, salt", 349),
    ("OLV-OIL", "Extra virgin olive oil", "extra virgin olive oil", 1499),
    ("SAG-FRS", "Fresh sage", "sage", 249),
    ("CRN-BRD", "Cornbread mix", "cornmeal, wheat flour, sugar, baking soda, salt", 229),
    ("AMD-MLK", "Almond milk", "filtered water, almonds, calcium carbonate, sea salt, gellan gum", 399),
]
BATCHES = [PRODUCTS[:12], PRODUCTS[12:]]
NEW_COLUMNS = "ingredient_count INT, nut_warning BOOLEAN"
BACKFILL = ("SET ingredient_count = LENGTH(ingredients) - LENGTH(REPLACE(ingredients, ',', '')) + 1, "
            "nut_warning = (" + " OR ".join(f"LOWER(ingredients) LIKE '%{n}%'" for n in NUTS) + ")")
ECU_PROPS = ("'delta.feature.catalogManaged' = 'supported', 'delta.enableRowTracking' = 'true', "
             "'delta.enableDeletionVectors' = 'true', 'delta.columnMapping.mode' = 'id', "
             "'delta.enableColumnUpdates-preview' = 'true'")
COLUMNS = "sku STRING, name STRING, ingredients STRING, price_cents INT"


# ---------- local reference
def reference() -> dict[str, list]:
    """sku -> [ingredient_count, nut_warning], computed in plain Python."""
    return {sku: [len(ing.split(",")), any(n in ing.lower() for n in NUTS)] for sku, _, ing, _ in PRODUCTS}


def _values(rows) -> str:
    return ", ".join("(" + ", ".join(_lit(v) if isinstance(v, str) else str(v) for v in r) + ")" for r in rows)


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def duckdb_values() -> dict[str, list]:
    """The same ALTER TABLE ... ADD COLUMN and UPDATE on DuckDB, as an independent engine."""
    import duckdb
    con = duckdb.connect()
    con.execute(f"CREATE TABLE catalog ({COLUMNS.replace('STRING', 'VARCHAR')})")
    for b in BATCHES:
        con.execute(f"INSERT INTO catalog VALUES {_values(b)}")
    for col in NEW_COLUMNS.split(", "):
        con.execute(f"ALTER TABLE catalog ADD COLUMN {col}")
    con.execute(f"UPDATE catalog {BACKFILL}")
    return {r[0]: [int(r[1]), bool(r[2])] for r in
            con.execute("SELECT sku, ingredient_count, nut_warning FROM catalog ORDER BY sku").fetchall()}


# ---------- Databricks statements
def steps(catalog: str) -> list[tuple[str, str]]:
    s = f"{catalog}.{SCHEMA}"
    out = [("schema", f"CREATE SCHEMA IF NOT EXISTS {s}")]
    for t, props in (("catalog_ecu", f" TBLPROPERTIES ({ECU_PROPS})"), ("catalog_std", "")):
        out.append((f"create_{t}", f"CREATE OR REPLACE TABLE {s}.{t} ({COLUMNS}){props}"))
        for i, b in enumerate(BATCHES, 1):
            out.append((f"load_{t}_{i}", f"INSERT INTO {s}.{t} VALUES {_values(b)}"))
        out.append((f"add_{t}", f"ALTER TABLE {s}.{t} ADD COLUMNS ({NEW_COLUMNS})"))
        out.append((f"backfill_{t}", f"UPDATE {s}.{t} {BACKFILL}"))
    return out


def rollback(catalog: str) -> list[tuple[str, str]]:
    t = f"{catalog}.{SCHEMA}.catalog_ecu"
    return [("ecu_off", f"ALTER TABLE {t} SET TBLPROPERTIES ('delta.enableColumnUpdates-preview' = 'false')"),
            ("purge", f"REORG TABLE {t} APPLY (PURGE)")]


def sql(catalog: str = "<catalog>") -> str:
    return ";\n".join(q for _, q in steps(catalog) + rollback(catalog)) + ";\n"


def _redact(text: str) -> str:
    host = os.environ.get("DATABRICKS_HOST", "").removeprefix("https://").rstrip("/")
    if host:
        text = text.replace(host, "<host>")
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<user>", text)
    return re.sub(r"\b\d{7,}\b", "<id>", text)


class Refused(Exception):
    pass


def _api(method: str, path: str, body: dict | None = None) -> dict:
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    if not host.startswith("https://"):
        host = "https://" + host
    req = urllib.request.Request(host + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise Refused(f"{method} {path.split('?')[0]} {e.code}: {e.read()[:300].decode(errors='replace')}")


def statement(wh: str, text: str, with_columns: bool = False):
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": text, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):  # up to ~5 more minutes (warehouse start)
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")
        raise RuntimeError(f"{res['status']['state']}: {msg[:450]}".replace("\n", " "))
    rows = res.get("result", {}).get("data_array", []) or []
    if with_columns:
        cols = [c["name"] for c in res.get("manifest", {}).get("schema", {}).get("columns", [])]
        return cols, rows
    return rows


def compute_refused(text: str) -> bool:
    t = text.lower()
    return ("feature_disabled" in t or "not active" in t or "cancelled" in t
            or "could not be processed by the warehouse" in t)


def preview_off(text: str) -> bool:
    t = text.lower()
    return "columnupdates" in t.replace("-", "").replace("_", "") or "column updates" in t


def parse_values(rows: list[list]) -> dict[str, list]:
    out = {}
    for sku, n, nut in rows:
        out[str(sku)] = [None if n is None else int(n),
                         None if nut is None else str(nut).lower() == "true"]
    return out


def update_metrics(cols: list[str], history_rows: list[list]) -> dict:
    """DESCRIBE HISTORY output -> the latest UPDATE's file metrics. Only version, operation and operationMetrics are
    read (the other columns hold user names and IDs, which are never printed)."""
    iv, io, im = cols.index("version"), cols.index("operation"), cols.index("operationMetrics")
    for r in sorted(history_rows, key=lambda r: -int(r[iv])):
        if r[io] == "UPDATE":
            try:
                m = json.loads(r[im]) if isinstance(r[im], str) else (r[im] or {})
            except ValueError:
                m = {}
            keys = ("numRemovedFiles", "numAddedFiles", "numAddedBytes", "numUpdatedRows", "numCopiedRows")
            return {k: int(m[k]) for k in keys if k in m and str(m[k]).isdigit()}
    return {}


def diff(got: dict[str, list], want: dict[str, list]) -> list[str]:
    if sorted(got) != sorted(want):
        return [f"skus back {len(got)}, expected {len(want)}"]
    return [f"{k}: {got[k]} != {want[k]}" for k in want if got[k] != want[k]]


def run_databricks() -> int:
    title = "Databricks day 50"
    want = reference()
    wh = catalog = None
    report: dict = {}
    try:
        whs = _api("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
        if not whs:
            print(f"::error title={title}::no SQL warehouse in the workspace")
            return 1
        wh = next((w for w in whs if w.get("enable_serverless_compute")), whs[0])["id"]
        catalog = statement(wh, "SELECT current_catalog()")[0][0]
        for _, q in steps(catalog):
            statement(wh, q)
        problems = []
        for t in ("catalog_ecu", "catalog_std"):
            fq = f"{catalog}.{SCHEMA}.{t}"
            got = parse_values(statement(wh, f"SELECT sku, ingredient_count, nut_warning FROM {fq} ORDER BY sku"))
            problems += [f"{t}: {p}" for p in diff(got, want)]
            report[t] = update_metrics(*statement(wh, f"DESCRIBE HISTORY {fq}", with_columns=True))
        props = dict(statement(wh, f"SHOW TBLPROPERTIES {catalog}.{SCHEMA}.catalog_ecu"))
        report["ecu_property"] = props.get("delta.enableColumnUpdates-preview")
        try:
            for _, q in rollback(catalog):
                statement(wh, q)
            got = parse_values(statement(
                wh, f"SELECT sku, ingredient_count, nut_warning FROM {catalog}.{SCHEMA}.catalog_ecu ORDER BY sku"))
            problems += [f"after rollback: {p}" for p in diff(got, want)]
            report["rollback"] = "ok"
        except RuntimeError as e:
            report["rollback"] = _redact(str(e))[:200]
    except (Refused, RuntimeError) as e:
        msg = _redact(str(e))
        if compute_refused(msg):
            print(f"::warning title={title}::the workspace refused ({msg[:300]}); the backfill was checked locally "
                  "only (pure Python and DuckDB)")
            return 0
        if preview_off(msg):
            print(f"::warning title={title}::efficient column updates are not available ({msg[:300]}); the feature "
                  "is Beta and a workspace admin must turn it on on the Previews page. Checked locally only")
            return 0
        print(f"::error title={title} failed::{msg[:700]}")
        return 1
    finally:
        if wh and catalog:
            try:
                statement(wh, f"DROP SCHEMA IF EXISTS {catalog}.{SCHEMA} CASCADE")
            except (Refused, RuntimeError) as e:
                print(f"::warning title={title} cleanup::{_redact(str(e))[:300]}")
    if problems:
        print(f"::error title={title} check failed::{_redact('; '.join(problems))[:800]}")
        return 1
    ecu, std = report.get("catalog_ecu", {}), report.get("catalog_std", {})
    print(f"::notice title={title}::ALTER TABLE ADD COLUMNS + one UPDATE backfilled ingredient_count and nut_warning "
          f"for {len(want)} products on an ECU table and a standard table; values equal the local reference on both "
          f"(and after the ECU rollback: {report.get('rollback')}). UPDATE metrics, ECU table {json.dumps(ecu)}; "
          f"standard table {json.dumps(std)}")
    if not ecu or ecu.get("numRemovedFiles", 1) > 0:
        print(f"::warning title={title}::the ECU table's UPDATE rewrote data files like the standard table "
              f"(property {report.get('ecu_property')}): Databricks fell back to a standard update, which the docs "
              "allow (the Beta may also be off on the Previews page)")
    print("DAY50_REPORT " + json.dumps(report))
    return 0


def summarise() -> dict:
    ref = reference()
    return {"products": len(PRODUCTS), "batches": len(BATCHES), "new_columns": NEW_COLUMNS.split(", "),
            "tables": ["catalog_ecu (ECU on)", "catalog_std (control)"],
            "nut_warnings": sum(v[1] for v in ref.values()),
            "ingredients_total": sum(v[0] for v in ref.values()),
            "single_ingredient_products": sum(v[0] == 1 for v in ref.values()),
            "duckdb_equals_reference": duckdb_values() == ref, "statements": len(steps("c") + rollback("c"))}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql())
    elif mode == "--databricks":
        raise SystemExit(run_databricks())
    else:
        s = summarise()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "pure Python + DuckDB ALTER/UPDATE (local) / efficient column updates on a serverless SQL "
                       "warehouse (cloud)", **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
