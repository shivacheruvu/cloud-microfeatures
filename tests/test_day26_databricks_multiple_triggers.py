"""Day 26 Databricks: the multiple-triggers rules, the reconciler, and the Jobs API client over HTTP against a local
server that follows the same rules (no credentials)."""
import importlib.util
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-26-store-refresh-triggers"
spec = importlib.util.spec_from_file_location("triggers26", FEATURE / "triggers.py")
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)


def test_rules():
    ok = {"triggers": [{"periodic": {"interval": 6, "unit": "HOURS"}}]}
    assert tr.problems(ok) == []
    assert tr.problems({"triggers": [{"file_arrival": {"url": "/Volumes/c/s/v/"}}] * 6})
    assert tr.problems({**ok, "continuous": {"pause_status": "UNPAUSED"}})
    assert tr.problems({"triggers": [{"file_arrival": {"url": "s3://bucket/x"}}]})
    assert tr.problems({"triggers": [{"file_arrival": {"url": "/Volumes/c/s/v/", "wait_after_last_change_seconds": 10}}]})
    assert tr.problems({"triggers": [{"periodic": {"interval": 1, "unit": "HOURS"},
                                      "schedule": {"quartz_cron_expression": "0 0 1 * * ?"}}]})


def test_plan_and_normalise():
    v = tr.config()["versions"]
    assert tr.plan(None, v[0]["triggers"]) == "create"
    assert tr.plan(v[0]["triggers"], v[0]["triggers"]) == "noop"
    # order and an explicit UNPAUSED default don't count as a change
    same = [{**t, "pause_status": "UNPAUSED"} for t in reversed(v[1]["triggers"])]
    assert tr.plan(v[1]["triggers"], same) == "noop"
    assert tr.plan(v[1]["triggers"], v[2]["triggers"]) == "update"


def test_local_scenario():
    s = tr.summarise(tr.scenario(tr.LocalJobs(), "workspace"))
    assert [x["call"] for x in s["steps"]] == ["create", "none", "update (3 triggers)", "update (4 triggers)",
                                               "update", "update", "delete"]
    assert s["refused_as_expected"] == 2 and s["noop_reapplies"] == 1
    # the refused updates left the v3 triggers alone, and the delete leaves nothing
    assert s["steps"][4]["triggers_after"] == s["steps"][3]["triggers_after"] == s["steps"][5]["triggers_after"]
    assert s["steps"][-1]["triggers_after"] is None
    v3 = s["versions"]["v3"]
    assert v3["paused"] == ["schedule"] and v3["live"] == 3 and v3["watched_folders"] == ["east", "west"]
    assert v3["scheduled_runs_per_week"] == 28 and s["versions"]["v1"]["scheduled_runs_per_week"] == 7


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    s = tr.summarise(tr.scenario(tr.LocalJobs(), "workspace"))
    assert {k: result[k] for k in s} == s


class FakeWorkspace(BaseHTTPRequestHandler):
    jobs = None
    seen = []
    accept_triggers = True
    created_paths = set()

    def log_message(self, *a):
        pass

    def _send(self, status, body=None):
        raw = json.dumps(body if body is not None else {}).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self):
        assert self.headers["Authorization"] == "Bearer test-token"
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        self.seen.append((self.command, u.path))
        body = json.loads(raw) if raw and self.headers["Content-Type"] == "application/json" else None
        return u.path, urllib.parse.parse_qs(u.query), body

    def do_GET(self):
        path, q, _ = self._route()
        if path.endswith("/current-metastore-assignment"):
            return self._send(200, {"default_catalog_name": "workspace"})
        s, settings = self.jobs.get(int(q["job_id"][0]))
        if s == 200:  # the server adds fields of its own; the reconciler must ignore them
            for t in settings.get("triggers", []):
                t.setdefault("pause_status", "UNPAUSED")
            return self._send(200, {"job_id": int(q["job_id"][0]), "settings": settings})
        self._send(400, {"error_code": "INVALID_PARAMETER_VALUE", "message": "Job 123456789 does not exist."})

    def do_POST(self):
        path, _, body = self._route()
        if path.startswith("/api/2.1/unity-catalog/"):
            return self._send(200, {"name": body["name"]})
        if path.endswith("/jobs/create"):
            if not self.accept_triggers:
                return self._send(400, {"error_code": "INVALID_PARAMETER_VALUE",
                                        "message": "Multiple triggers is not enabled for workspace 1234567890"})
            assert body["tasks"][0]["spark_python_task"]["python_file"] in self.created_paths
            s, jid = self.jobs.create(body)
            return self._send(s, {"job_id": jid} if s == 200 else {"error_code": "INVALID_PARAMETER_VALUE"})
        if path.endswith("/jobs/update"):
            s = self.jobs.update(body["job_id"], body["new_settings"])
            return self._send(s, {} if s == 200 else {"error_code": "INVALID_PARAMETER_VALUE"})
        if path.endswith("/jobs/delete"):
            s = self.jobs.delete(body["job_id"])
            return self._send(s, {} if s == 200 else {"error_code": "RESOURCE_DOES_NOT_EXIST"})
        self._send(404)

    def do_PUT(self):
        path, _, _ = self._route()
        self.created_paths.add(path.removeprefix("/api/2.0/fs/files").removeprefix("/api/2.0/fs/directories"))
        self._send(204)

    def do_DELETE(self):
        self._route()
        self._send(200)


def _serve(monkeypatch, accept=True):
    FakeWorkspace.jobs, FakeWorkspace.seen, FakeWorkspace.accept_triggers = tr.LocalJobs(), [], accept
    FakeWorkspace.created_paths = set()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWorkspace)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(tr, "SCHEME", "http")
    monkeypatch.setenv("DATABRICKS_HOST", f"127.0.0.1:{server.server_port}")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-token")
    return server


def test_rest_client_over_http_matches_local(monkeypatch, capsys):
    server = _serve(monkeypatch)
    try:
        assert tr.main("--databricks") == 0
    finally:
        server.shutdown()
    out = capsys.readouterr().out
    assert "::notice" in out and "matched local on all 7 steps" in out and "::error" not in out
    assert FakeWorkspace.jobs.jobs == {}
    deletes = [p for m, p in FakeWorkspace.seen if m == "DELETE"]
    assert deletes[-1] == "/api/2.1/unity-catalog/schemas/workspace.mf26_prices"
    assert "/Volumes/workspace/mf26_prices/price_drop/east" in FakeWorkspace.created_paths


def test_triggers_not_enabled_is_a_warning(monkeypatch, capsys):
    server = _serve(monkeypatch, accept=False)
    try:
        assert tr.main("--databricks") == 0
    finally:
        server.shutdown()
    out = capsys.readouterr().out
    assert "::warning" in out and "Multiple Triggers" in out and "1234567890" not in out
    assert ("DELETE", "/api/2.1/unity-catalog/schemas/workspace.mf26_prices") in FakeWorkspace.seen


def test_inactive_account_is_a_warning(monkeypatch, capsys):
    def refuse():
        raise tr.ApiError(403, "PERMISSION_DENIED", "Organization <id> has been cancelled or is not active yet.")
    monkeypatch.setattr(tr, "run_databricks", refuse)
    assert tr.main("--databricks") == 0
    assert "::warning" in capsys.readouterr().out


def test_unexpected_errors_fail(monkeypatch, capsys):
    def boom():
        raise tr.ApiError(500, "INTERNAL_ERROR")
    monkeypatch.setattr(tr, "run_databricks", boom)
    assert tr.main("--databricks") == 1
    assert "::error" in capsys.readouterr().out
