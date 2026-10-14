"""Day 9 (Databricks): audit the saved grocery report queries with the new SQL function `parse_sql`
(Databricks SQL 2026.36): which ones don't parse any more, and which tables each one reads.

  python audit.py --local        # DuckDB's json_serialize_sql parses every query; writes result.json
  python audit.py --sql          # print the Databricks statement
  python audit.py --databricks   # serverless SQL warehouse via the Statement Execution API: parse_sql() on every
                                 # query, compare with local (nothing is created, nothing is executed)

The expected verdicts and table lists are declared in data/reports.csv, and both engines are checked against them:
  * errors:   a query that should parse is reported as broken (or the reverse); a parsed query's output doesn't
              mention a table it reads
  * notices:  the top-level keys parse_sql returns, for a valid and for a broken query (to document its shape)
Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed
about the workspace except the query results.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def reports() -> list[dict]:
    with open(HERE / "data" / "reports.csv", newline="") as f:
        return [{"name": r["name"], "tables": sorted(r["tables"].split()), "sql": r["sql"],
                 "parses": bool(r["tables"].strip())} for r in csv.DictReader(f)]


# ---------- local (DuckDB's parser)
def _walk(node, fn):
    fn(node)
    if isinstance(node, dict):
        for v in node.values():
            _walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            _walk(v, fn)


def duckdb_tables(ast: dict) -> list[str]:
    """Base tables read by a parsed statement, without the CTE names it defines."""
    tables, ctes = set(), set()

    def visit(n):
        if isinstance(n, dict):
            if n.get("type") == "BASE_TABLE" and n.get("table_name"):
                tables.add(n["table_name"].lower())
            for entry in (n.get("cte_map") or {}).get("map", []) if isinstance(n.get("cte_map"), dict) else []:
                ctes.add(entry["key"].lower())
    _walk(ast, visit)
    return sorted(tables - ctes)


def run_local(reps: list[dict]) -> list[dict]:
    import duckdb
    con = duckdb.connect()
    out = []
    for r in reps:
        parsed = json.loads(con.execute("SELECT json_serialize_sql(?)", [r["sql"]]).fetchone()[0])
        broken = bool(parsed.get("error"))
        out.append({"name": r["name"], "parses": not broken,
                    "tables": [] if broken else duckdb_tables(parsed),
                    "error": parsed.get("error_message", "") if broken else ""})
    return out


# ---------- Databricks parse_sql output (its JSON shape is read defensively)
ERROR_TEXT = re.compile(r"PARSE_SYNTAX_ERROR|syntax error|parse error|ParseException", re.I)


def _strings(node) -> list[str]:
    acc = []
    _walk(node, lambda n: acc.append(n) if isinstance(n, str) else None)
    return acc


def is_broken(parsed) -> bool:
    """Broken when parse_sql returns nothing, a truthy error-like key, or parser error text."""
    if parsed is None:
        return True
    flagged = []

    def visit(n):
        if isinstance(n, dict):
            flagged.extend(k for k, v in n.items() if "error" in k.lower() and v not in (None, False, "", [], {}))
    _walk(parsed, visit)
    return bool(flagged) or any(ERROR_TEXT.search(s) for s in _strings(parsed))


def mentions(parsed, table: str) -> bool:
    word = re.compile(rf"(^|[^a-z0-9_]){re.escape(table)}($|[^a-z0-9_])", re.I)
    return any(word.search(s) for s in _strings(parsed))


def parse_cloud(rows: list[list]) -> dict[str, object]:
    out = {}
    for name, text in rows:
        try:
            out[name] = json.loads(text) if text is not None else None
        except json.JSONDecodeError:
            out[name] = {"unparsed_output": text}
    return out


# ---------- Databricks SQL
def sql_statement(reps: list[dict]) -> str:
    for r in reps:
        assert "'" not in r["sql"] and "\\" not in r["sql"], r["name"]  # inlined as plain string literals
    values = ",\n".join(f"    ('{r['name']}', '{r['sql']}')" for r in reps)
    body = "\n".join(l for l in (HERE / "audit.sql").read_text().splitlines() if not l.lstrip().startswith("--"))
    return body.strip().rstrip(";").replace("{reports}", values)


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
        print("::error title=Databricks day 9::No SQL warehouse in the workspace")
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
        print(f"::error title=Databricks day 9 parse_sql {res['status']['state']}::{msg}")
        raise SystemExit(1)
    return res.get("result", {}).get("data_array", []) or []


# ---------- checks and summary
def check_local(local: list[dict], reps: list[dict]) -> list[str]:
    exp = {r["name"]: r for r in reps}
    return [f"{l['name']}: local parses={l['parses']} tables={l['tables']}, expected parses={exp[l['name']]['parses']} "
            f"tables={exp[l['name']]['tables']}" for l in local
            if l["parses"] != exp[l["name"]]["parses"] or l["tables"] != exp[l["name"]]["tables"]]


def compare(cloud: dict[str, object], reps: list[dict]) -> list[str]:
    errors = []
    for r in reps:
        if r["name"] not in cloud:
            errors.append(f"{r['name']}: no parse_sql row")
            continue
        parsed = cloud[r["name"]]
        if is_broken(parsed) == r["parses"]:
            errors.append(f"{r['name']}: parse_sql says {'broken' if r['parses'] else 'valid'}, local says "
                          f"{'valid' if r['parses'] else 'broken'}")
        elif r["parses"]:
            missing = [t for t in r["tables"] if not mentions(parsed, t)]
            if missing:
                errors.append(f"{r['name']}: parse_sql output doesn't mention {missing}")
    return errors


def shape(parsed) -> list[str]:
    return sorted(parsed.keys()) if isinstance(parsed, dict) else [type(parsed).__name__]


def summarise(local: list[dict]) -> dict:
    lineage: dict[str, list[str]] = {}
    for l in local:
        for t in l["tables"]:
            lineage.setdefault(t, []).append(l["name"])
    broken = [l["name"] for l in local if not l["parses"]]
    return {
        "queries": len(local),
        "parse": len(local) - len(broken),
        "broken": len(broken),
        "broken_queries": broken,
        "tables": len(lineage),
        "lineage": dict(sorted(lineage.items(), key=lambda kv: (-len(kv[1]), kv[0]))),
        "table": [{"query": l["name"], "parses": l["parses"], "tables": l["tables"], "error": l["error"]}
                  for l in local],
        "headline": f"{len(broken)} of {len(local)} saved report queries no longer parse; the other "
                    f"{len(local) - len(broken)} read {len(lineage)} tables",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    reps = reports()
    if mode == "--sql":
        print(sql_statement(reps) + ";")
    elif mode == "--databricks":
        cloud = parse_cloud(statement(_warehouse(), sql_statement(reps)))
        errors = compare(cloud, reps)
        valid = next(r["name"] for r in reps if r["parses"])
        bad = next(r["name"] for r in reps if not r["parses"])
        print(f"::notice title=Databricks day 9 parse_sql shape::valid query keys {shape(cloud.get(valid))}; "
              f"broken query keys {shape(cloud.get(bad))}; broken output "
              f"{json.dumps(cloud.get(bad))[:300]}")
        if errors:
            print(f"::error title=Databricks result differs from local::{json.dumps(errors)[:900]}")
            raise SystemExit(1)
        s = summarise(run_local(reps))
        print("::notice title=Databricks day 9::parse_sql() ran on a serverless SQL warehouse and matched local: "
              f"{s['headline']}; broken {s['broken_queries']}; lineage "
              f"{json.dumps({t: len(q) for t, q in s['lineage'].items()})}")
    else:
        local = run_local(reps)
        errors = check_local(local, reps)
        if errors:
            raise SystemExit("\n".join(errors))
        summary = summarise(local)
        (HERE / "result.json").write_text(json.dumps({"engine": "DuckDB json_serialize_sql (local) / Databricks SQL "
                                                      "parse_sql() (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
