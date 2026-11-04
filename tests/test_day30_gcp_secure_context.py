"""Day 30 Google Cloud: one shelf-price view scoped per request by SECURE_CONTEXT. The view SQL itself runs on DuckDB
(SECURE_CONTEXT('k') swapped for the caller's literal) and must equal the Python reference; result.json is current;
the cloud runner builds the dataset, sends secureContext per caller, checks the documented failures and always drops
the dataset (against a fake jobs.query endpoint)."""
import importlib.util
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-30-store-scoped-prices"
spec = importlib.util.spec_from_file_location("day30_store_view", FEATURE / "store_view.py")
sv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sv)
RESULT = json.loads((FEATURE / "result.json").read_text())
CTX = dict(sv.CALLERS)


def _reference_views():
    return {label: sv.view_reference(sv.table_rows(), ctx) for label, ctx in sv.CALLERS}


def test_result_json_is_current():
    got = json.loads(json.dumps(sv.summarise(_reference_views())))
    assert {k: v for k, v in RESULT.items() if k != "engine"} == got


def _substitute(sql: str, ctx: dict) -> str:
    def lit(m):
        key = m.group(1)
        if key not in ctx:
            raise KeyError(key)        # BigQuery fails the query on a missing key, so do we
        return "'" + ctx[key] + "'"
    return re.sub(r"SECURE_CONTEXT\('(\w+)'\)", lit, sql)


def test_view_sql_on_duckdb_matches_reference():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    con.execute("CREATE TABLE store_prices (store_id VARCHAR, sku VARCHAR, item VARCHAR, shelf_price DECIMAL(10,2),"
                " on_promo BOOLEAN, unit_cost DECIMAL(10,2))")
    con.executemany("INSERT INTO store_prices VALUES (?, ?, ?, ?, ?, ?)",
                    [[r[c] for c in ("store_id", "sku", "item", "shelf_price", "on_promo", "unit_cost")]
                     for r in sv.table_rows()])
    for label, ctx in sv.CALLERS:
        body = _substitute(sv.VIEW_BODY.format(dataset="x"), ctx).replace("`x.store_prices`", "store_prices")
        q = f"SELECT {', '.join(sv.COLUMNS)} FROM ({body}) ORDER BY sku"
        rows = [dict(zip(sv.COLUMNS, r)) for r in con.execute(q).fetchall()]
        for r in rows:  # DuckDB rounds NUMERIC division the same way (half away from zero) once cast
            if r["margin_pct"] is not None:
                r["margin_pct"] = Decimal(str(r["margin_pct"])).quantize(Decimal("0.1"))
        assert sv.canonical(rows) == RESULT["rows_by_caller"][label], label
    with pytest.raises(KeyError):
        _substitute(sv.VIEW_BODY, {"store_id": "east"})


def test_findings():
    c = {x["caller"]: x for x in RESULT["callers"]}
    assert RESULT["table_rows"] == 24 and RESULT["views"] == 1
    assert [c[k]["rows"] for k in ("east clerk", "west clerk", "north clerk", "north buyer")] == [8, 8, 8, 8]
    assert c["unknown store"]["rows"] == 0
    assert not any(c[k]["cost_visible"] for k in ("east clerk", "west clerk", "north clerk"))
    assert c["north buyer"]["cost_visible"] and c["north buyer"]["avg_margin_pct"] == "36.2"
    assert c["north buyer"]["basket_total"] == c["north clerk"]["basket_total"] == "43.42"
    assert RESULT["rows_by_caller"]["east clerk"][0] == ["0001", "oat milk", "3.82", True, None, None]


def test_sql_shape():
    s = sv.table_sql("d")
    assert s.count("STRUCT(") == 24 and "NUMERIC '3.89'" in s and "NUMERIC '4.69'" in s
    assert sv.VIEW_BODY.count("SECURE_CONTEXT(") == 3 and "store_id = SECURE_CONTEXT('store_id')" in sv.VIEW_BODY
    q = sv.caller_sql("d")
    assert "SECURE_CONTEXT" not in q and "north" not in q and "buyer" not in q


class FakeBQ:
    """Answers jobs.query like BigQuery would, using the reference view for SELECTs against the view."""

    def __init__(self, wrong=False, lenient=False):
        self.bodies, self.wrong, self.lenient = [], wrong, lenient

    def __call__(self, body):
        self.bodies.append(body)
        q, ctx = body["query"], (body.get("secureContext") or {}).get("secureParameterEntries")
        if q.startswith(("CREATE", "DROP")):
            return {"jobComplete": True}
        if q.startswith("SELECT SECURE_CONTEXT") and not self.lenient:
            raise sv.QueryError("HTTP 400: SECURE_CONTEXT is only supported in logical views (project my-proj-123)")
        if ctx is None or "role" not in ctx or "store_id" not in ctx:
            if self.lenient:
                return {"jobComplete": True, "schema": {"fields": []}, "rows": []}
            raise sv.QueryError("HTTP 400: Secure context parameter role was not provided")
        rows = sv.view_reference(sv.table_rows(), ctx)
        if self.wrong and ctx["role"] == "clerk":
            rows = [{**r, "unit_cost": r["shelf_price"]} for r in rows]   # a leak
        types = {"sku": "STRING", "item": "STRING", "shelf_price": "NUMERIC", "on_promo": "BOOLEAN",
                 "unit_cost": "NUMERIC", "margin_pct": "NUMERIC"}

        def cell(v):
            return None if v is None else ("true" if v is True else "false" if v is False else str(v))
        return {"jobComplete": True, "schema": {"fields": [{"name": k, "type": types[k]} for k in sv.COLUMNS]},
                "rows": [{"f": [{"v": cell(r[k])} for k in sv.COLUMNS]} for r in rows]}


def test_cloud_run_against_fake_endpoint(capsys, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "my-proj-123")
    fake = FakeBQ()
    assert sv.cloud(fake, dataset="my-proj-123.mf_day30_1") == 0
    out = capsys.readouterr().out
    assert "all equal to the local reference" in out and out.count("refused as documented") == 3
    assert "my-proj-123" not in out
    sent = [b.get("secureContext") for b in fake.bodies]
    assert {"secureParameterEntries": {"store_id": "north", "role": "buyer"}} in sent
    assert fake.bodies[-1]["query"].startswith("DROP SCHEMA IF EXISTS")
    assert all(b["maximumBytesBilled"] == "20971520" and b["useQueryCache"] is False for b in fake.bodies)


def test_cloud_run_flags_a_cost_leak_and_still_drops(capsys, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "my-proj-123")
    fake = FakeBQ(wrong=True)
    assert sv.cloud(fake, dataset="my-proj-123.mf_day30_1") == 1
    assert "differs from local" in capsys.readouterr().out
    assert fake.bodies[-1]["query"].startswith("DROP SCHEMA")


def test_accepted_misuse_is_a_warning_not_a_failure(capsys, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "my-proj-123")
    assert sv.cloud(FakeBQ(lenient=True), dataset="my-proj-123.mf_day30_1") == 0
    assert capsys.readouterr().out.count("::warning title=Google Cloud day 30 rule") == 3
