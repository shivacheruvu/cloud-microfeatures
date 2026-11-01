"""Day 27 Databricks: the read_files XML reference (explicit schema + rescued data column), the cloud row normaliser,
and the REST client over HTTP against a local server (no credentials)."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-27-supplier-asn-xml"
spec = importlib.util.spec_from_file_location("asn27", FEATURE / "asn_xml.py")
asn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asn)


def test_casts():
    assert asn._cast("24", "INT") == 24
    assert asn._cast("2 cs", "INT") is None
    assert asn._cast("2.1", "DECIMAL(8,2)") == "2.10"
    assert asn._cast("3,49", "DECIMAL(8,2)") is None
    assert asn._cast("1234567.00", "DECIMAL(8,2)") is None  # 9 digits > precision 8


def test_parse_rescues_misfits_and_unknown_fields():
    rows = asn.parse_file("x.xml", '<d><line no="1"><sku>A</sku><qty>2 cs</qty><unit_cost>1.00</unit_cost>'
                                   '<lot>L1</lot></line></d>')
    assert rows == [{"file": "x.xml", "_no": 1, "sku": "A", "qty": None, "unit_cost": "1.00",
                     "rescued": {"qty": "2 cs", "lot": "L1"}}]


def test_local_summary_matches_result_json():
    s = asn.summarise(asn.run_local())
    assert (s["files"], s["lines"], s["rescued_lines"], s["null_without_rescue"]) == (3, 16, 3, 2)
    assert {x["supplier"]: x["value"] for x in s["suppliers"]} == {
        "Hearth Street Bakery": "122.45", "Prairie Dairy Co-op": "361.88", "Riverbend Produce": "193.76"}
    saved = json.loads((FEATURE / "result.json").read_text())
    assert {k: saved[k] for k in s} == s


def test_query_sql():
    sql = asn.query_sql("/Volumes/workspace/microfeatures_day27/asn/")
    assert "format => 'xml'" in sql and "rowTag => 'line'" in sql and "rescuedDataColumn => '_rescued'" in sql
    assert "{path}" not in sql and not sql.startswith("--")
    try:
        asn.query_sql("/Volumes/x'; DROP TABLE t; --/a/b/")
        assert False
    except ValueError:
        pass


def test_normalise_cloud_drops_file_path():
    local = asn.run_local()
    cloud = []
    for r in local:
        resc = dict(r["rescued"] or {})
        if resc:
            resc["_file_path"] = "dbfs:/Volumes/c/s/asn/" + r["file"]
        cloud.append([r["file"], str(r["_no"]), r["sku"], None if r["qty"] is None else str(r["qty"]),
                      r["unit_cost"], json.dumps(resc) if resc else None])
    assert asn.normalise_cloud(list(reversed(cloud))) == local
    assert asn.compare(asn.normalise_cloud(cloud), local) == 0


def test_compute_refused_and_redact():
    assert asn.compute_refused(400, "BAD_REQUEST", "The request could not be processed by the warehouse.")
    assert asn.compute_refused(403, "PERMISSION_DENIED", "Organization 1234567 has been cancelled or is not active yet")
    assert not asn.compute_refused(400, "INVALID_PARAMETER_VALUE", "bad schema")
    assert asn.redact("org 1234567890 at h.example.com", "h.example.com") == "org <id> at <host>"


class Fake(BaseHTTPRequestHandler):
    calls: list = []

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
        self.rfile.read(n)
        Fake.calls.append((self.command, self.path.split("?")[0]))
        p = self.path
        if p.endswith("current-metastore-assignment"):
            return self._send(200, {"default_catalog_name": "workspace"})
        if p.startswith("/api/2.0/sql/warehouses"):
            return self._send(200, {"warehouses": [{"id": "w1", "enable_serverless_compute": True}]})
        if p == "/api/2.0/sql/statements":
            return self._send(400, {"error_code": "BAD_REQUEST",
                                    "message": "The request could not be processed by the warehouse."})
        return self._send(200)

    do_GET = do_POST = do_PUT = do_DELETE = _handle


def test_refused_warehouse_is_a_warning_and_cleans_up(monkeypatch, capsys):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("DATABRICKS_HOST", f"127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-token")
    monkeypatch.setattr(asn, "SCHEME", "http")
    Fake.calls = []
    try:
        assert asn.main("--databricks") == 0
    finally:
        srv.shutdown()
    out = capsys.readouterr().out
    assert "::warning title=Databricks day 27::" in out and "127.0.0.1" not in out
    puts = [p for m, p in Fake.calls if m == "PUT"]
    assert len(puts) == 3 and all(p.startswith("/api/2.0/fs/files/Volumes/workspace/microfeatures_day27/asn/")
                                  for p in puts)
    assert ("DELETE", "/api/2.1/unity-catalog/schemas/workspace.microfeatures_day27") in Fake.calls
