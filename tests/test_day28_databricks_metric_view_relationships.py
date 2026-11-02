"""Day 28 Databricks: relationship parsing (SQL and DESCRIBE output), the audit, the generated SQL, and the REST
client over HTTP against a local server, for both a refused warehouse and a full simulated run (no credentials)."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-28-basket-join-contracts"
spec = importlib.util.spec_from_file_location("mvrel28", FEATURE / "contracts.py")
mv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mv)

DESCRIBE = [
    ["barcode", "string", None], ["store_id", "string", None], ["", "", ""],
    ["# Detailed Table Information", "", ""], ["Type", "METRIC_VIEW", ""], ["", "", ""],
    ["# Constraints", "", ""],
    ["basket_product", "MANY TO ONE RELATIONSHIP TO `workspace`.`microfeatures_day28`.`products_mv` USING (`barcode`)", ""],
    ["basket_store", "MANY TO ONE RELATIONSHIP TO `workspace`.`microfeatures_day28`.`stores_mv` USING (store_id)", ""],
]


def test_declared_relationships_from_sql_and_describe_agree():
    local = mv.declared_local()
    assert [(r["name"], r["cardinality"], r["target"], r["keys"]) for r in local] == [
        ("basket_product", "MANY TO ONE", "products_mv", ["barcode"]),
        ("basket_store", "MANY TO ONE", "stores_mv", ["store_id"])]
    assert mv.declared_from_describe(DESCRIBE, "basket_mv") == local


def test_parse_alias_and_on_condition():
    r = mv.parse_relationship("ONE TO MANY RELATIONSHIP TO main.s.orders_mv AS o USING (customer_id, day)", "c_mv", "x")
    assert (r["cardinality"], r["target"], r["keys"]) == ("ONE TO MANY", "orders_mv", ["customer_id", "day"])
    try:
        mv.parse_relationship("MANY TO ONE RELATIONSHIP TO c AS c ON a.x = c.y", "a")
        assert False
    except ValueError:
        pass


def test_audit_finds_fanout_and_orphans():
    rel = {"name": "r", "source": "basket_mv", "cardinality": "MANY TO ONE", "target": "products_mv", "keys": ["k"]}
    src = {("a",): {"rows": 2, "value": mv.Decimal("10")}, ("b",): {"rows": 1, "value": mv.Decimal("3")}}
    tgt = {("a",): {"rows": 3, "value": mv.Decimal("0")}}
    a = mv.audit(rel, src, tgt)
    assert a["fanout_overcount"] == "20.00" and a["dropped_by_inner_join"] == "3.00" and not a["holds"]
    assert mv.audit(rel, {("a",): src[("a",)]}, {("a",): {"rows": 1, "value": 0}})["holds"]


def test_local_summary_matches_result_json():
    s = mv.summarise(mv.run_local())
    assert (s["relationships_declared"], s["relationships_holding"], s["basket_revenue"]) == (2, 0, "91.10")
    by = {a["relationship"]: a for a in s["audits"]}
    assert by["basket_product"]["fanout_overcount"] == "24.43"
    assert [o["key"] for o in by["basket_product"]["orphan_keys"]] == ["0009"]
    assert by["basket_store"]["dropped_by_inner_join"] == "14.27"
    saved = json.loads((FEATURE / "result.json").read_text())
    assert {k: saved[k] for k in s} == s


def test_sql_is_safe_and_has_no_leading_comment():
    for s in mv.statements():
        assert not s.startswith("--") and "{schema}" not in s
    assert "RELATIONSHIP TO microfeatures_day28.products_mv" in mv.statements()[3]
    assert "MEASURE(revenue)" in mv.grouped_sql("basket_mv", ["barcode"])
    try:
        mv.grouped_sql("basket_mv", ["x; DROP"])
        assert False
    except ValueError:
        pass
    assert "CAST('O''Brien' AS STRING)" == mv._lit("O'Brien", "STRING")


def test_compute_refused_and_redact():
    assert mv.compute_refused(400, "BAD_REQUEST", "The request could not be processed by the warehouse.")
    assert mv.compute_refused(403, "PERMISSION_DENIED", "Organization 1234567 has been cancelled or is not active yet")
    assert not mv.compute_refused(400, "INVALID_PARAMETER_VALUE", "bad schema")
    assert mv.redact("org 1234567890 at h.example.com", "h.example.com") == "org <id> at <host>"


def _rows_for(sql):
    """Simulated warehouse answers, computed from the CSVs the way the metric view queries would."""
    if sql.startswith("DESCRIBE"):
        return DESCRIBE
    if "information_schema" in sql:
        return [["basket_product"], ["basket_store"]]
    if sql.startswith("SELECT"):
        view = sql.split(" FROM microfeatures_day28.")[1].split()[0]
        key = sql.split()[1].rstrip(",")
        g = mv.grouped_local(view, [key])
        return [[k[0], str(v["rows"]), str(v["value"]) if view in mv.SOURCE_VALUE else "0"] for k, v in sorted(g.items())]
    return []


class Fake(BaseHTTPRequestHandler):
    refuse = True
    sql: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, body=None):
        raw = json.dumps(body or {}).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.startswith("/api/2.0/sql/warehouses"):
            return self._send(200, {"warehouses": [{"id": "w1", "enable_serverless_compute": True}]})
        if self.path == "/api/2.0/sql/statements":
            Fake.sql.append(body["statement"])
            if Fake.refuse:
                return self._send(400, {"error_code": "BAD_REQUEST",
                                        "message": "The request could not be processed by the warehouse."})
            return self._send(200, {"statement_id": "s", "status": {"state": "SUCCEEDED"},
                                    "result": {"data_array": _rows_for(body["statement"])}})
        return self._send(404)

    do_GET = do_POST = _handle


def _run(monkeypatch, refuse):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("DATABRICKS_HOST", f"127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-token")
    monkeypatch.setattr(mv, "SCHEME", "http")
    Fake.refuse, Fake.sql = refuse, []
    try:
        return mv.main("--databricks")
    finally:
        srv.shutdown()


def test_refused_warehouse_is_a_warning(monkeypatch, capsys):
    assert _run(monkeypatch, refuse=True) == 0
    out = capsys.readouterr().out
    assert "::warning title=Databricks day 28::" in out and "127.0.0.1" not in out


def test_simulated_cloud_run_matches_local_and_drops_schema(monkeypatch, capsys):
    assert _run(monkeypatch, refuse=False) == 0
    out = capsys.readouterr().out
    assert "::notice title=Databricks day 28::" in out and "overcount revenue by 24.43" in out
    assert "information_schema lists 2" in out
    assert sum("ADD CONSTRAINT" in s for s in Fake.sql) == 2
    assert Fake.sql[-1] == "DROP SCHEMA IF EXISTS microfeatures_day28 CASCADE"
