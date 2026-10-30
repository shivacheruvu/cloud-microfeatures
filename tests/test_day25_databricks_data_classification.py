"""Day 25 Databricks: the Data Classification scope reconciler plans create / masked update / no-op correctly, reports
the personal-data columns each policy version leaves unscanned, and its REST client sends the documented calls:
checked by playing the scenario over HTTP against a local server that follows the Data Classification API v1 rules."""
import importlib.util
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-25-pii-scan-scope"
spec = importlib.util.spec_from_file_location("scan_scope", FEATURE / "scan_scope.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def test_plan():
    v1 = ss.desired_config({"included_schemas": ["pos", "loyalty"]}, "c")
    assert v1 == {"name": "catalogs/c/config", "included_schemas": {"names": ["loyalty", "pos"]}}
    assert ss.plan(None, v1) == ("create", [])
    assert ss.plan(v1, v1) == ("noop", [])
    v3 = ss.desired_config({"excluded_schemas": ["sandbox"]}, "c")
    assert ss.plan(v1, v3) == ("update", ["included_schemas", "excluded_schemas"])
    with pytest.raises(ValueError):
        ss.desired_config({"included_schemas": ["a"], "excluded_schemas": ["b"]}, "c")


def test_local_scenario():
    run = ss.scenario(ss.LocalApi(), "grocery")
    assert [s["outcome"] for s in run["steps"]] == ["absent", "ok", "rejected (exists)", "ok", "ok", "ok", "ok"]
    assert run["steps"][5]["scope_after"] == {"excluded_schemas": ["sandbox"]}
    assert run["steps"][-1]["scope_after"] is None
    s = ss.summarise(run)
    assert s["write_calls"] == 5 and s["noop_reapplies"] == 1
    v = s["versions"]
    assert len(v["v1"]["pii_like_columns_missed"]) == 6
    assert v["v2"]["pii_like_columns_missed"] == v["v3"]["pii_like_columns_missed"] == ["sandbox.analyst_scratch.email"]
    assert "pricing" in v["v3"]["schemas_scanned"] and "pricing" not in v["v2"]["schemas_scanned"]


class FakeApi(BaseHTTPRequestHandler):
    api = None
    seen = []
    catalog_status = 200
    sent = []  # every config body the client sent

    def log_message(self, *a):
        pass

    def _send(self, status, body=None):
        raw = json.dumps(body if body is not None else ({} if status < 300 else {"error_code": "X"})).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self):
        assert self.headers["Authorization"] == "Bearer test-token"
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        self.seen.append((self.command, u.path, u.query))
        return u.path, urllib.parse.parse_qs(u.query), body

    def do_GET(self):
        path, _, _ = self._route()
        if path.endswith("/current-metastore-assignment"):
            return self._send(200, {"default_catalog_name": "workspace"})
        if path == "/api/2.1/unity-catalog/schemas":
            return self._send(200, {"schemas": [{"name": n} for n in ("default", "information_schema", "shiva_data")]})
        s, cfg = self.api.get(path.removeprefix(ss.API + "/"))
        self._send(s, cfg)

    def do_POST(self):
        path, _, body = self._route()
        if path == "/api/2.1/unity-catalog/catalogs":
            return self._send(self.catalog_status, {"error_code": "INVALID_STATE"} if self.catalog_status != 200
                              else {"name": body["name"]})
        if path.startswith("/api/2.1/unity-catalog/"):
            return self._send(200, {"name": body["name"]})
        self.sent.append(body)
        s, cfg = self.api.create(path.removeprefix(ss.API + "/").removesuffix("/config"), body)
        self._send(s, cfg)

    def do_PATCH(self):
        path, q, body = self._route()
        self.sent.append(body)
        s, cfg = self.api.update(path.removeprefix(ss.API + "/"), body, q["update_mask"][0].split(","))
        self._send(s, cfg)

    def do_DELETE(self):
        path, _, _ = self._route()
        if path.startswith("/api/2.1/unity-catalog/"):
            return self._send(200)
        self._send(self.api.delete(path.removeprefix(ss.API + "/")))


def _serve(monkeypatch, catalog_status):
    FakeApi.api, FakeApi.seen, FakeApi.sent, FakeApi.catalog_status = ss.LocalApi(), [], [], catalog_status
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(ss, "SCHEME", "http")
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    monkeypatch.setenv("DATABRICKS_HOST", f"127.0.0.1:{server.server_port}")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-token")
    return server


def test_fallback_on_default_catalog_never_scopes_existing_schemas(monkeypatch):
    server = _serve(monkeypatch, 400)
    try:
        assert ss.run_databricks() == ss.scenario(ss.LocalApi(), ss.SCRATCH_CATALOG)
    finally:
        server.shutdown()
    assert len(FakeApi.sent) == 4  # create, duplicate create, two PATCHes
    for body in FakeApi.sent:
        assert body["name"] == "catalogs/workspace/config"
        inc = (body.get("included_schemas") or {}).get("names", [])
        exc = (body.get("excluded_schemas") or {}).get("names", [])
        assert all(n.startswith("mf25_") for n in inc)
        if exc:  # everything that existed before is opted out, so only scratch schemas are in scope
            assert {"default", "information_schema", "shiva_data", "mf25_sandbox"} <= set(exc)
    deletes = [p for m, p, _ in FakeApi.seen if m == "DELETE"]
    assert sorted(p.rsplit(".", 1)[-1] for p in deletes if "/schemas/" in p) == sorted(
        "mf25_" + n for n in ss.catalog()["schemas"])
    assert FakeApi.api.configs == {}


def test_rest_client_over_http_matches_local(monkeypatch):
    FakeApi.api, FakeApi.seen, FakeApi.catalog_status = ss.LocalApi(), [], 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(ss, "SCHEME", "http")
        monkeypatch.setattr(ss.time, "sleep", lambda s: None)
        monkeypatch.setenv("DATABRICKS_HOST", f"127.0.0.1:{server.server_port}")
        monkeypatch.setenv("DATABRICKS_TOKEN", "test-token")
        assert ss.run_databricks() == ss.scenario(ss.LocalApi(), ss.SCRATCH_CATALOG)
    finally:
        server.shutdown()
    patches = [s for s in FakeApi.seen if s[0] == "PATCH"]
    assert [urllib.parse.parse_qs(p[2])["update_mask"] for p in patches] == [
        ["included_schemas"], ["included_schemas,excluded_schemas"]]
    assert all(p[1] == f"{ss.API}/catalogs/{ss.SCRATCH_CATALOG}/config" for p in patches)
    assert FakeApi.seen[-1][:2] == ("DELETE", f"/api/2.1/unity-catalog/catalogs/{ss.SCRATCH_CATALOG}")
    assert FakeApi.api.configs == {}


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    summary = ss.summarise(ss.scenario(ss.LocalApi(), ss.SCRATCH_CATALOG))
    assert {k: result[k] for k in summary} == summary
