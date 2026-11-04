"""Day 30 (Google Cloud): one shelf-price view for every store, scoped per request with SECURE_CONTEXT
(Preview, BigQuery release notes 2026-11-02).

  python store_view.py               # local: pure-Python reference of what each caller should see, writes result.json
  python store_view.py --sql         # print the table + view DDL (dataset `<dataset>`) and the per-caller query
  python store_view.py --cloud       # cloud.sh: build a scratch dataset, query the view as five callers over REST,
                                     # check each answer equals the reference, then drop the dataset

A store app calls BigQuery with `secureContext: {"secureParameterEntries": {"store_id": "north", "role": "clerk"}}`
in the jobs.query body. The view reads those values with SECURE_CONTEXT('store_id') / SECURE_CONTEXT('role'):
rows are filtered to that store, and unit cost and margin are shown only to the `buyer` role (NULL for clerks).
One view instead of one view per store and role. The docs' rules this checks: SECURE_CONTEXT works only inside
logical views (a top-level call is an error), values are STRINGs, and a missing key fails the query.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORES = ["east", "west", "north"]
# sku, item, shelf price, unit cost
ITEMS = [
    ("0001", "oat milk", "4.49", "2.95"), ("0002", "whole milk", "3.89", "2.71"),
    ("0005", "bananas", "0.59", "0.31"), ("0008", "sourdough", "5.49", "2.40"),
    ("0010", "eggs", "3.29", "2.36"), ("0013", "salmon", "10.99", "7.80"),
    ("0017", "coffee", "9.99", "6.10"), ("0021", "ice cream", "5.49", "3.05"),
]
# store: (price change in cents vs the list price, skus on promo at 15% off)
STORE_SHAPE = {"east": (0, {"0001", "0021"}), "west": (20, {"0013"}), "north": (-10, set())}
# the callers the cloud run plays, in order: (label, secure context entries)
CALLERS = [
    ("east clerk", {"store_id": "east", "role": "clerk"}),
    ("west clerk", {"store_id": "west", "role": "clerk"}),
    ("north clerk", {"store_id": "north", "role": "clerk"}),
    ("north buyer", {"store_id": "north", "role": "buyer"}),
    ("unknown store", {"store_id": "south", "role": "clerk"}),
]
COLUMNS = ["sku", "item", "shelf_price", "on_promo", "unit_cost", "margin_pct"]
BYTES_CAP = 20971520


def table_rows() -> list[dict]:
    """24 rows: 3 stores x 8 SKUs. Unit cost is the same everywhere (one supplier), shelf prices differ."""
    out = []
    for store in STORES:
        cents, promos = STORE_SHAPE[store]
        for sku, item, price, cost in ITEMS:
            p = Decimal(price) + Decimal(cents) / 100
            if sku in promos:
                p = (p * Decimal("0.85")).quantize(Decimal("0.01"))
            out.append({"store_id": store, "sku": sku, "item": item, "shelf_price": p, "on_promo": sku in promos,
                        "unit_cost": Decimal(cost)})
    return out


def view_reference(rows: list[dict], ctx: dict) -> list[dict]:
    """What the view returns for one caller (the same logic as VIEW_SQL, in Python)."""
    buyer = ctx["role"] == "buyer"
    out = []
    for r in sorted((r for r in rows if r["store_id"] == ctx["store_id"]), key=lambda r: r["sku"]):
        margin = ((r["shelf_price"] - r["unit_cost"]) * 100 / r["shelf_price"]).quantize(Decimal("0.1"), ROUND_HALF_UP)
        out.append({"sku": r["sku"], "item": r["item"], "shelf_price": r["shelf_price"], "on_promo": r["on_promo"],
                    "unit_cost": r["unit_cost"] if buyer else None, "margin_pct": margin if buyer else None})
    return out


def _lit(v) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, Decimal):
        return f"NUMERIC '{v}'"
    return "'" + str(v).replace("'", "\\'") + "'"


def table_sql(dataset: str) -> str:
    rows = ",\n  ".join("STRUCT(" + ", ".join(f"{_lit(r[c])} AS {c}" for c in
                                            ("store_id", "sku", "item", "shelf_price", "on_promo", "unit_cost")) + ")"
                        for r in table_rows())
    return f"CREATE OR REPLACE TABLE `{dataset}.store_prices` AS\nSELECT * FROM UNNEST([\n  {rows}\n])"


VIEW_BODY = """SELECT
  sku,
  item,
  shelf_price,
  on_promo,
  IF(SECURE_CONTEXT('role') = 'buyer', unit_cost, NULL) AS unit_cost,
  IF(SECURE_CONTEXT('role') = 'buyer',
     ROUND((shelf_price - unit_cost) * 100 / shelf_price, 1), NULL) AS margin_pct
FROM `{dataset}.store_prices`
WHERE store_id = SECURE_CONTEXT('store_id')"""


def view_sql(dataset: str) -> str:
    return f"CREATE OR REPLACE VIEW `{dataset}.my_store_prices` AS\n" + VIEW_BODY.format(dataset=dataset)


def caller_sql(dataset: str) -> str:
    """What every store app runs: no store id or role anywhere in the SQL."""
    return f"SELECT {', '.join(COLUMNS)} FROM `{dataset}.my_store_prices` ORDER BY sku"


def canonical(rows: list[dict]) -> list[list]:
    """Comparable form: Decimals as normalised strings (BigQuery JSON sends NUMERIC as plain strings)."""
    def c(v):
        if v is None or isinstance(v, bool):
            return v
        if isinstance(v, Decimal):
            return format(v.normalize(), "f")
        return str(v)
    return [[c(r[k]) for k in COLUMNS] for r in rows]


def summarise(views: dict[str, list[dict]]) -> dict:
    callers = []
    for label, ctx in CALLERS:
        rows = views[label]
        callers.append({
            "caller": label, "store_id": ctx["store_id"], "role": ctx["role"], "rows": len(rows),
            "promos": sum(r["on_promo"] for r in rows),
            "basket_total": str(sum((r["shelf_price"] for r in rows), Decimal("0"))),
            "cost_visible": any(r["unit_cost"] is not None for r in rows),
            "avg_margin_pct": (str((sum(r["margin_pct"] for r in rows) / len(rows)).quantize(Decimal("0.1")))
                               if rows and rows[0]["margin_pct"] is not None else None),
        })
    return {"table_rows": len(table_rows()), "views": 1, "callers": callers,
            "rows_by_caller": {k: canonical(v) for k, v in views.items()}}


# ---- cloud ----------------------------------------------------------------------------------------------------
class QueryError(Exception):
    pass


def _redact(text: str) -> str:
    import re
    p = os.environ.get("GCP_PROJECT_ID", "")
    if p:
        text = text.replace(p, "<project>")
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<account>", text)
    return re.sub(r"\b\d{6,}\b", "<number>", text)


def _token() -> str:
    return subprocess.run(["gcloud", "auth", "print-access-token"], check=True, capture_output=True,
                          text=True).stdout.strip()


def http_post(body: dict) -> dict:
    project = os.environ["GCP_PROJECT_ID"]
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise QueryError(f"HTTP {e.code}: {e.read()[:700].decode(errors='replace')}") from None


def run_query(post, query: str, ctx: dict | None = None) -> dict:
    body = {"query": query, "useLegacySql": False, "location": "US", "maximumBytesBilled": str(BYTES_CAP),
            "timeoutMs": 120000, "useQueryCache": False}
    if ctx is not None:
        body["secureContext"] = {"secureParameterEntries": ctx}
    resp = post(body)
    if not resp.get("jobComplete"):
        raise QueryError("query didn't finish inside jobs.query's wait")
    return resp


def parse_rows(resp: dict) -> list[dict]:
    fields = [f["name"] for f in resp["schema"]["fields"]]
    types = {f["name"]: f["type"] for f in resp["schema"]["fields"]}
    out = []
    for row in resp.get("rows") or []:
        r = {}
        for name, cell in zip(fields, row["f"]):
            v = cell["v"]
            if v is None:
                r[name] = None
            elif types[name] in ("NUMERIC", "BIGNUMERIC"):
                r[name] = Decimal(v)
            elif types[name] in ("BOOLEAN", "BOOL"):
                r[name] = v == "true"
            else:
                r[name] = v
        out.append(r)
    return out


def expect_error(post, query: str, ctx: dict | None) -> str | None:
    """Run a query the docs say must fail; return the error text, or None if BigQuery accepted it."""
    try:
        run_query(post, query, ctx)
    except QueryError as e:
        return _redact(str(e))
    return None


def cloud(post=None, dataset: str | None = None) -> int:
    post = post or http_post
    project = os.environ.get("GCP_PROJECT_ID", "p")
    dataset = dataset or f"{project}.mf_day30_{int(time.time())}"
    local = json.loads((HERE / "result.json").read_text())
    try:
        run_query(post, f"CREATE SCHEMA `{dataset}` OPTIONS (location = 'US', default_table_expiration_days = 0.1,"
                        " labels = [('project', 'ai-frontier')])")
    except QueryError as e:
        print(f"::error title=Google Cloud day 30 dataset failed::{_redact(str(e))}")
        return 1
    try:
        run_query(post, table_sql(dataset))
        run_query(post, view_sql(dataset))
        views = {label: parse_rows(run_query(post, caller_sql(dataset), ctx)) for label, ctx in CALLERS}
        no_ctx = expect_error(post, caller_sql(dataset), None)
        no_role = expect_error(post, caller_sql(dataset), {"store_id": "east"})
        top_level = expect_error(post, "SELECT SECURE_CONTEXT('store_id') AS s", {"store_id": "east", "role": "x"})
    except QueryError as e:
        print(f"::error title=Google Cloud day 30 query failed::{_redact(str(e))}")
        return 1
    finally:
        try:
            run_query(post, f"DROP SCHEMA IF EXISTS `{dataset}` CASCADE")
        except QueryError as e:
            print(f"::warning title=Google Cloud day 30 cleanup::{_redact(str(e))} (the tables expire in 2.4 h)")
    got = summarise(views)
    errors = [f"{label}: cloud rows {json.dumps(got['rows_by_caller'][label])[:400]}"
              for label, _ in CALLERS if got["rows_by_caller"][label] != local["rows_by_caller"][label]]
    for e in errors:
        print(f"::error title=Google Cloud day 30 differs from local::{_redact(e)}")
    for what, err in (("no secureContext at all", no_ctx), ("secureContext without role", no_role),
                      ("top-level SECURE_CONTEXT (not in a view)", top_level)):
        if err is None:   # the docs say this fails; a finding, not a broken feature
            print(f"::warning title=Google Cloud day 30 rule::{what} was accepted, the docs say it should fail")
        else:
            print(f"::notice title=Google Cloud day 30 refused as documented::{what}: {err[:300]}")
    if errors:
        return 1
    c = {x["caller"]: x for x in got["callers"]}
    print("::notice title=Google Cloud day 30::one view, SECURE_CONTEXT per request: "
          + "; ".join(f"{x['caller']} saw {x['rows']} rows (basket ${x['basket_total']}, cost "
                      f"{'shown' if x['cost_visible'] else 'hidden'})" for x in got["callers"])
          + f"; north buyer avg margin {c['north buyer']['avg_margin_pct']}%; all equal to the local reference")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--sql"]:
        print(table_sql("<dataset>") + ";\n\n" + view_sql("<dataset>") + ";\n\n" + caller_sql("<dataset>") + ";")
        return 0
    if argv[:1] == ["--cloud"]:
        return cloud()
    rows = table_rows()
    out = {"engine": "pure-Python reference (local) / BigQuery logical view with SECURE_CONTEXT, queried over "
                     "jobs.query with secureContext (cloud)",
           **summarise({label: view_reference(rows, ctx) for label, ctx in CALLERS})}
    (HERE / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out["callers"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
