"""Day 49 (Google Cloud): audit row-level security on a store price table with
**INFORMATION_SCHEMA.ROW_ACCESS_POLICIES** (GA in the BigQuery release notes of 2026-11-18).

A chain keeps every store's shelf prices in one table and decides who sees which rows with row access policies.
The question an auditor asks is "which rows does each policy actually open up, and is anything dead or uncovered?"
Until now the policies could only be listed one table at a time (`bq ls --row_access_policies`); the view returns
every policy in a dataset or region with its filter predicate, in SQL.

This run creates three policies on a 12-row price table, reads them back from the view, and **replays each stored
filter predicate in DuckDB** over the same rows, so the audit is computed from the metadata alone. The cloud then
queries the protected table and must see exactly the union the replay predicts.

  python audit.py                      # local: replay the defined predicates in DuckDB; writes result.json
  python audit.py --sql PART DATASET   # print BigQuery SQL (PART: setup, policies, rows)
  python audit.py --compare POLICIES ROWS   # check bq's output (cloud.sh)

Must hold (else the run fails): the view lists exactly the 3 policies on the table; each stored predicate, replayed
in DuckDB, opens the same rows as its definition (6, 2 and 0); the protected table returns exactly the 8 rows in
their union, and none of the 4 south everyday rows that no policy covers.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
TABLE = "store_prices"
GRANTEE = "__RUNNER__"   # cloud.sh substitutes the runner's own service account; never printed

# store, region, sku, price_cents, on_promo
ROWS = [
    ("S1", "north", "MILK-2L", 189, False), ("S1", "north", "BREAD-WHITE", 135, True),
    ("S1", "north", "EGGS-12", 349, False), ("S2", "north", "MILK-2L", 195, False),
    ("S2", "north", "BREAD-WHITE", 139, False), ("S2", "north", "EGGS-12", 299, True),
    ("S3", "south", "MILK-2L", 179, True), ("S3", "south", "BREAD-WHITE", 129, False),
    ("S3", "south", "EGGS-12", 339, False), ("S4", "south", "MILK-2L", 185, False),
    ("S4", "south", "BREAD-WHITE", 125, False), ("S4", "south", "EGGS-12", 289, True),
]
COLS = ("store_id", "region", "sku", "price_cents", "on_promo")

# name -> filter predicate (written in SQL that BigQuery and DuckDB read the same way)
POLICIES = {
    "north_stores": "region = 'north'",
    "south_promos": "region = 'south' AND on_promo",
    "closed_store_s9": "store_id = 'S9'",
}


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"CREATE TABLE {TABLE} (store_id VARCHAR, region VARCHAR, sku VARCHAR, price_cents INTEGER, "
                "on_promo BOOLEAN)")
    con.executemany(f"INSERT INTO {TABLE} VALUES (?, ?, ?, ?, ?)", ROWS)
    return con


def normalise(pred: str) -> str:
    """BigQuery may quote names with backticks or wrap the predicate in brackets; DuckDB needs neither."""
    p = re.sub(r"\s+", " ", pred.replace("`", "")).strip()
    while p.startswith("(") and p.endswith(")") and _balanced(p[1:-1]):
        p = p[1:-1].strip()
    return p


def _balanced(s: str) -> bool:
    depth = 0
    for ch in s:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth < 0:
            return False
    return depth == 0


def replay(pred: str) -> list[tuple[str, str]]:
    """Rows (store_id, sku) a filter predicate opens up, evaluated in DuckDB over the local copy."""
    return _con().execute(f"SELECT store_id, sku FROM {TABLE} WHERE {normalise(pred)} ORDER BY 1, 2").fetchall()


def expected() -> dict:
    per = {name: replay(p) for name, p in POLICIES.items()}
    union = sorted({r for rows in per.values() for r in rows})
    allrows = sorted((r[0], r[2]) for r in ROWS)
    return {"per_policy": per, "visible": union, "uncovered": [r for r in allrows if r not in union]}


# ---------- SQL
def _q(s: str) -> str:
    assert "'" not in s and "\\" not in s
    return f"'{s}'"


def sql(part: str, ds: str) -> str:
    if part == "setup":
        rows = ",\n    ".join(
            f"STRUCT({_q(s)} AS store_id, {_q(r)} AS region, {_q(k)} AS sku, {c} AS price_cents, "
            f"{str(p).upper()} AS on_promo)" for s, r, k, c, p in ROWS)
        return f"""-- Day 49 setup: one price table for four stores (row access policies are added next)
CREATE OR REPLACE TABLE `{ds}`.{TABLE} AS
SELECT * FROM UNNEST([
    {rows}
]);
"""
    if part == "policies":
        stmts = [f"CREATE OR REPLACE ROW ACCESS POLICY {n} ON `{ds}`.{TABLE}\n"
                 f"  GRANT TO (\"serviceAccount:{GRANTEE}\")\n  FILTER USING ({p});" for n, p in POLICIES.items()]
        return "-- Day 49: three row access policies, all granted to the runner\n" + "\n".join(stmts) + "\n"
    if part == "view":
        return f"""-- Day 49: read every policy on the table back from the new view (GA 2026-11-18)
SELECT * FROM `{ds}`.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES
WHERE table_name = '{TABLE}'
"""
    if part == "view-region":
        return f"""-- Day 49: same, region-qualified form of the view
SELECT * FROM `region-us`.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES
WHERE table_schema = '{ds}' AND table_name = '{TABLE}'
"""
    if part == "rows":
        return f"""-- Day 49: what the runner can see through the policies
SELECT store_id, sku FROM `{ds}`.{TABLE} ORDER BY store_id, sku
"""
    raise SystemExit(f"unknown part {part}")


# ---------- cloud output
def _redact(text: str) -> str:
    text = text.replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
    text = re.sub(r"[\w.+-]+@[\w.-]+", "<account>", text)
    return re.sub(r"\d{6,}", "<number>", text)


def read_rows(path: str) -> list[dict] | None:
    text = Path(path).read_text()
    dec = json.JSONDecoder()
    for i in (k for k, ch in enumerate(text) if ch == "["):
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, list) and all(isinstance(r, dict) for r in obj):
            return obj
    return None


def _key(row: dict, *needles: str) -> str | None:
    for needle in needles:
        for k in row:
            if needle in k.lower():
                return k
    return None


def check(policies: list[dict], rows: list[dict]) -> tuple[list[str], dict]:
    exp, problems, info = expected(), [], {"columns": sorted(policies[0]) if policies else []}
    name_k = _key(policies[0], "policy_name", "row_access_policy_id", "policy") if policies else None
    pred_k = _key(policies[0], "filter_predicate", "predicate", "filter") if policies else None
    if not policies or not name_k or not pred_k:
        return [f"view returned {len(policies)} rows with columns {info['columns']}; need a policy name and a "
                "filter predicate column"], info
    got = {str(p[name_k]): str(p[pred_k] or "") for p in policies}
    if sorted(got) != sorted(POLICIES):
        problems.append(f"view lists policies {sorted(got)}, expected {sorted(POLICIES)}")
    info["stored_predicates"], info["text_same"] = {}, 0
    for name, pred in got.items():
        info["stored_predicates"][name] = _redact(pred)[:120]
        if name not in POLICIES:
            continue
        info["text_same"] += normalise(pred).lower() == POLICIES[name].lower()
        try:
            opened = replay(pred)
        except duckdb.Error as e:
            problems.append(f"{name}: stored predicate {_redact(pred)[:80]!r} does not replay in DuckDB: {str(e)[:120]}")
            continue
        if opened != exp["per_policy"][name]:
            problems.append(f"{name}: stored predicate opens {len(opened)} rows, definition opens "
                            f"{len(exp['per_policy'][name])}")
    gk = _key(policies[0], "grantee")
    info["grantee_column"] = gk
    if gk:
        info["grantees_per_policy"] = sorted({len(p[gk]) if isinstance(p[gk], list) else 1 for p in policies})
    seen = sorted((str(r.get("store_id")), str(r.get("sku"))) for r in rows)
    if seen != exp["visible"]:
        leaked = [r for r in seen if r in exp["uncovered"]]
        problems.append(f"runner sees {len(seen)} rows, replay predicts {len(exp['visible'])}"
                        + (f"; uncovered rows leaked: {leaked}" if leaked else ""))
    return problems, info


def compare(policies_path: str, rows_path: str) -> int:
    pol, rows = read_rows(policies_path), read_rows(rows_path)
    if pol is None or rows is None:
        bad = policies_path if pol is None else rows_path
        print(f"::error title=Unreadable BigQuery output::{_redact(Path(bad).read_text()[:300])!r}")
        return 1
    problems, info = check(pol, rows)
    if problems:
        print("::error title=Google Cloud day 49 policy audit failed::" + _redact("; ".join(problems))[:900])
        return 1
    s = summarise()
    print(f"::notice title=Google Cloud day 49::INFORMATION_SCHEMA.ROW_ACCESS_POLICIES listed all {s['policies']} "
          f"policies on the price table with their filter predicates ({info['text_same']}/{s['policies']} stored "
          f"exactly as written). Replayed in DuckDB they open {s['per_policy']['north_stores']}, "
          f"{s['per_policy']['south_promos']} and {s['per_policy']['closed_store_s9']} rows (closed_store_s9 is "
          f"dead); the runner saw exactly the {s['visible']} predicted rows of {s['rows']}, none of the "
          f"{s['uncovered']} uncovered south everyday rows. View columns: {', '.join(info['columns'])[:300]}.")
    return 0


def summarise() -> dict:
    e = expected()
    return {"rows": len(ROWS), "policies": len(POLICIES),
            "per_policy": {n: len(r) for n, r in e["per_policy"].items()},
            "visible": len(e["visible"]), "uncovered": len(e["uncovered"]),
            "dead_policies": [n for n, r in e["per_policy"].items() if not r],
            "uncovered_rows": [f"{s}/{k}" for s, k in e["uncovered"]]}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql(sys.argv[2], sys.argv[3]))
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2], sys.argv[3]))
    else:
        s = summarise()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "DuckDB replay of the policy predicates (local) / BigQuery row access policies + "
                       "INFORMATION_SCHEMA.ROW_ACCESS_POLICIES (cloud)", **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
