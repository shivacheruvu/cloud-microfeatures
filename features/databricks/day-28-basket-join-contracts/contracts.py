"""Day 28 (Databricks): declare the joins between grocery metric views as relationships (Beta, 2026-10-30), then
audit each declared relationship against the data, because Databricks records relationships but never checks them.

  python contracts.py --local        # pure-Python reference: reads the relationships declared in metric_views.sql,
                                     # audits them over data/*.csv, writes result.json
  python contracts.py --databricks   # creates scratch tables + three metric views on a serverless SQL warehouse
                                     # (Statement Execution API), declares the relationships with ALTER VIEW ... ADD
                                     # CONSTRAINT, reads them back with DESCRIBE TABLE EXTENDED, audits them with
                                     # metric view queries, checks everything equals the local run, drops the schema

For a "... TO ONE" relationship the target key must be unique (otherwise a join fans out and double counts the
source measure); for a "ONE TO ..." relationship the source key must be unique; every source key should find a
target row (otherwise the join drops it). Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub
Actions secrets); the hostname and long digit runs are redacted from anything printed.
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
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "microfeatures_day28"
SCHEME = "https"
CENT = Decimal("0.01")
SOURCE_VALUE = {"basket_mv": "revenue"}  # the money measure each source view carries, for fan-out and drop impact
VIEW_TABLE = {"basket_mv": "basket_lines", "products_mv": "products", "stores_mv": "stores"}
TABLE_TYPES = {
    "basket_lines": [("basket_id", "STRING"), ("store_id", "STRING"), ("barcode", "STRING"), ("qty", "INT"),
                     ("unit_price", "DECIMAL(6,2)")],
    "products": [("barcode", "STRING"), ("name", "STRING"), ("department", "STRING")],
    "stores": [("store_id", "STRING"), ("city", "STRING")],
}
REL_RE = re.compile(
    r"(?:CONSTRAINT\s+`?(?P<name>\w+)`?\s+)?(?P<src_card>MANY|ONE)\s+TO\s+(?P<tgt_card>MANY|ONE)\s+RELATIONSHIP\s+TO\s+"
    r"(?P<target>[`\w.]+)(?:\s+(?:AS\s+)?`?(?P<alias>\w+)`?)??\s+(?:USING\s*\((?P<using>[^)]*)\)|ON\s+(?P<on>.+))",
    re.I | re.S)


# ---------------------------------------------------------------- relationships
def _short(name: str) -> str:
    return name.replace("`", "").split(".")[-1].lower()


def parse_relationship(text: str, source: str, name: str | None = None) -> dict | None:
    m = REL_RE.search(" ".join(text.split()))
    if not m:
        return None
    if not m.group("using"):
        raise ValueError("only USING relationships are audited here")
    return {"name": (name or m.group("name") or "").lower(), "source": _short(source),
            "cardinality": f"{m.group('src_card').upper()} TO {m.group('tgt_card').upper()}",
            "target": _short(m.group("target")),
            "keys": [k.strip().strip("`").lower() for k in m.group("using").split(",")]}


def statements(schema: str = SCHEMA) -> list[str]:
    text = (HERE / "metric_views.sql").read_text().replace("{schema}", schema)
    body = "\n".join(line for line in text.splitlines() if not line.startswith("--"))
    return [s.strip() for s in re.split(r"^;\s*$", body, flags=re.M) if s.strip()]


def declared_local() -> list[dict]:
    """The relationships metric_views.sql declares, parsed from its ALTER VIEW statements."""
    out = []
    for s in statements():
        m = re.match(r"ALTER VIEW\s+([\w.`]+)\s+ADD\s+(.*)", s, re.I | re.S)
        if m:
            out.append(parse_relationship(m.group(2), m.group(1)))
    return sorted(out, key=lambda r: r["name"])


def declared_from_describe(rows: list[list], source: str) -> list[dict]:
    """DESCRIBE TABLE EXTENDED rows (col_name, data_type, comment): the '# Constraints' section lists each relationship
    as name + 'MANY TO ONE RELATIONSHIP TO `cat`.`schema`.`target` USING (...)'."""
    out, in_constraints = [], False
    for row in rows:
        first = (row[0] or "").strip()
        if first.startswith("#"):
            in_constraints = first.lower().startswith("# constraints")
            continue
        text = " ".join(str(c) for c in row[1:] if c)
        if in_constraints and first and "RELATIONSHIP" in text.upper():
            rel = parse_relationship(text, source, first)
            if rel:
                out.append(rel)
    return out


# ---------------------------------------------------------------- data + audit
def load(table: str) -> list[dict]:
    with open(HERE / "data" / f"{table}.csv", newline="") as f:
        return list(csv.DictReader(f))


def _value(row: dict) -> Decimal:
    return int(row["qty"]) * Decimal(row["unit_price"])


def grouped_local(view: str, keys: list[str]) -> dict[tuple, dict]:
    """What `SELECT keys, MEASURE(row_count), MEASURE(<value>) FROM view GROUP BY ALL` returns, from the CSVs."""
    out: dict[tuple, dict] = defaultdict(lambda: {"rows": 0, "value": Decimal("0")})
    for r in load(VIEW_TABLE[view]):
        g = out[tuple(r[k] for k in keys)]
        g["rows"] += 1
        if view in SOURCE_VALUE:
            g["value"] += _value(r)
    return dict(out)


def audit(rel: dict, source: dict[tuple, dict], target: dict[tuple, dict]) -> dict:
    src_one, tgt_one = rel["cardinality"].startswith("ONE"), rel["cardinality"].endswith("ONE")
    dup_target = {k: g["rows"] for k, g in target.items() if tgt_one and g["rows"] > 1}
    dup_source = {k: g["rows"] for k, g in source.items() if src_one and g["rows"] > 1}
    orphans = {k: g for k, g in source.items() if k not in target}
    fanout = sum((source[k]["value"] * (n - 1) for k, n in dup_target.items() if k in source), Decimal("0"))
    key = lambda k: "|".join(k)
    return {
        "relationship": rel["name"], "declared": f"{rel['source']} {rel['cardinality']} {rel['target']}",
        "keys": rel["keys"],
        "one_side_duplicates": [{"key": key(k), "rows": n} for k, n in sorted({**dup_target, **dup_source}.items())],
        "orphan_keys": [{"key": key(k), "rows": g["rows"], "value": str(g["value"].quantize(CENT))}
                        for k, g in sorted(orphans.items())],
        "fanout_overcount": str(fanout.quantize(CENT)),
        "dropped_by_inner_join": str(sum((g["value"] for g in orphans.values()), Decimal("0")).quantize(CENT)),
        "holds": not dup_target and not dup_source and not orphans,
    }


def run_local() -> dict:
    rels = declared_local()
    audits = [audit(r, grouped_local(r["source"], r["keys"]), grouped_local(r["target"], r["keys"])) for r in rels]
    return {"declared": rels, "audits": audits}


def summarise(res: dict) -> dict:
    total = sum((_value(r) for r in load("basket_lines")), Decimal("0")).quantize(CENT)
    a = res["audits"]
    return {"relationships_declared": len(res["declared"]), "relationships_holding": sum(x["holds"] for x in a),
            "basket_revenue": str(total), "audits": a}


# ---------------------------------------------------------------- Databricks
class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code} {message}".strip())
        self.status, self.code, self.message = status, code, message


class ComputeRefused(Exception):
    """The workspace refused compute (warehouse or account state), not a defect in this feature."""


def _host() -> str:
    return os.environ["DATABRICKS_HOST"].rstrip("/").removeprefix("https://")


def redact(text: str, host: str = "") -> str:
    text = str(text)
    if host:
        text = text.replace(host, "<host>")
    return re.sub(r"\d{6,}", "<id>", " ".join(text.split()))[:300]


def _request(method: str, path: str, body=None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}", "Content-Type": "application/json"}
    req = urllib.request.Request(f"{SCHEME}://{_host()}{path}", method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        out, status = e.read(), e.code
    try:
        return status, (json.loads(out) if out.strip() else {})
    except ValueError:
        return status, {}


def _call(method, path, body=None) -> dict:
    s, out = _request(method, path, body)
    if s != 200:
        raise ApiError(s, out.get("error_code", ""), redact(out.get("message", ""), _host()))
    return out


def compute_refused(status: int, code: str, message: str) -> bool:
    """Refusals seen since 2026-10-24 on the Free Edition workspace (warehouse 400, FEATURE_DISABLED, inactive org)."""
    m = message.lower()
    return (code == "FEATURE_DISABLED" or "feature_disabled" in m or "not active" in m or "cancelled" in m
            or (status == 400 and "could not be processed by the warehouse" in m))


def _warehouse() -> str:
    whs = _call("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
    if not whs:
        raise ComputeRefused("no SQL warehouse in the workspace")
    return next((w for w in whs if w.get("enable_serverless_compute")), whs[0])["id"]


def statement(wh: str, sql: str) -> list[list]:
    s, res = _request("POST", "/api/2.0/sql/statements",
                      {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    if s != 200:
        code, msg = res.get("error_code", ""), res.get("message", "")
        if compute_refused(s, code, msg):
            raise ComputeRefused(redact(f"{s} {code} {msg}", _host()))
        raise ApiError(s, code, redact(msg, _host()))
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _call("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        err = res["status"].get("error", {})
        if compute_refused(400, err.get("error_code", ""), err.get("message", "")):
            raise ComputeRefused(redact(err.get("message", ""), _host()))
        raise ApiError(400, res["status"]["state"], redact(err.get("message", ""), _host()))
    return list((res.get("result") or {}).get("data_array", []) or [])


def _lit(v: str, typ: str) -> str:
    return f"CAST('{v.replace(chr(39), chr(39) * 2)}' AS {typ})"


def table_sql(table: str, schema: str = SCHEMA) -> str:
    cols = TABLE_TYPES[table]
    rows = ",\n  ".join("(" + ", ".join(_lit(r[c], t) for c, t in cols) + ")" for r in load(table))
    return (f"CREATE OR REPLACE TABLE {schema}.{table} AS SELECT * FROM VALUES\n  {rows}\n"
            f"AS t({', '.join(c for c, _ in cols)})")


def grouped_sql(view: str, keys: list[str], schema: str = SCHEMA) -> str:
    if not all(re.fullmatch(r"\w+", k) for k in keys) or view not in VIEW_TABLE:
        raise ValueError("unexpected relationship key or view")
    ks = ", ".join(keys)
    value = f", MEASURE({SOURCE_VALUE[view]}) AS v" if view in SOURCE_VALUE else ", 0 AS v"
    return (f"SELECT {ks}, n, v FROM (SELECT {ks}, MEASURE(row_count) AS n{value} "
            f"FROM {schema}.{view} GROUP BY ALL) ORDER BY {ks}")


def grouped_cloud(rows: list[list], nkeys: int) -> dict[tuple, dict]:
    return {tuple(r[:nkeys]): {"rows": int(r[nkeys]), "value": Decimal(r[nkeys + 1] or "0")} for r in rows}


def run_databricks() -> tuple[dict, int]:
    wh = _warehouse()
    statement(wh, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA} COMMENT 'cloud-microfeatures day 28 scratch'")
    try:
        for t in TABLE_TYPES:
            statement(wh, table_sql(t))
        for s in statements():
            statement(wh, s)
        sources = sorted({r["source"] for r in declared_local()})
        rels = sorted((rel for src in sources
                       for rel in declared_from_describe(statement(wh, f"DESCRIBE TABLE EXTENDED {SCHEMA}.{src}"), src)),
                      key=lambda r: r["name"])
        try:
            listed = len(statement(wh, "SELECT constraint_name FROM information_schema.table_constraints "
                                       f"WHERE table_schema = '{SCHEMA}' AND constraint_type = 'RELATIONSHIP'"))
        except ApiError:
            listed = -1
        audits = []
        for r in rels:
            n = len(r["keys"])
            audits.append(audit(r, grouped_cloud(statement(wh, grouped_sql(r["source"], r["keys"])), n),
                                grouped_cloud(statement(wh, grouped_sql(r["target"], r["keys"])), n)))
        return {"declared": rels, "audits": audits}, listed
    finally:  # leave nothing behind
        try:
            statement(wh, f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        except (ApiError, ComputeRefused) as e:
            print(f"::warning title=Databricks day 28 cleanup::dropping the scratch schema returned {e}")


def compare(cloud: dict, local: dict, listed: int) -> int:
    if cloud != local:
        for part in ("declared", "audits"):
            if cloud[part] != local[part]:
                print(f"::error title=Databricks day 28 differs from local::{part}: cloud "
                      f"{json.dumps(cloud[part])[:400]} local {json.dumps(local[part])[:400]}")
        return 1
    s = summarise(cloud)
    parts = []
    for a in s["audits"]:
        bits = []
        if a["one_side_duplicates"]:
            bits.append(f"{len(a['one_side_duplicates'])} duplicate key(s) on the ONE side, a join would overcount "
                        f"revenue by {a['fanout_overcount']}")
        if a["orphan_keys"]:
            bits.append(f"{len(a['orphan_keys'])} orphan key(s), an inner join would drop {a['dropped_by_inner_join']}")
        parts.append(f"{a['relationship']} ({a['declared']}): " + ("; ".join(bits) or "holds"))
    info = f"; information_schema lists {listed}" if listed >= 0 else ""
    print(f"::notice title=Databricks day 28::metric view relationships matched local: {s['relationships_declared']} "
          f"declared and read back with DESCRIBE TABLE EXTENDED{info}, {s['relationships_holding']} hold on the data. "
          + " | ".join(parts))
    return 0


def main(mode: str) -> int:
    local = run_local()
    if mode != "--databricks":
        s = summarise(local)
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "Python reference of metric view relationship audit (local) / Databricks serverless SQL "
                       "warehouse with Unity Catalog metric views (cloud)", **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
        return 0
    try:
        cloud, listed = run_databricks()
    except ComputeRefused as e:
        print(f"::warning title=Databricks day 28::the workspace refused compute ({e}); the account looks inactive, "
              "so metric view relationships were verified locally only (nothing was created)")
        return 0
    except ApiError as e:
        if compute_refused(e.status, e.code, e.message):
            print(f"::warning title=Databricks day 28::the workspace refused the call ({e}); verified locally only")
            return 0
        print(f"::error title=Databricks day 28 API call failed::{e}")
        return 1
    return compare(cloud, local, listed)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "--local"))
