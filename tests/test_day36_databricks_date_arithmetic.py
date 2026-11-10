"""Day 36 Databricks: the date-arithmetic resolver follows the documented syntax, examples and limits; the markdown
plan uses the resolved dates; and the cloud runner (over HTTP against a local fake workspace) accepts values that
match the run's start time, catches a wrong one, and warns on unresolved references or an inactive account."""
import importlib.util
import json
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-36-markdown-window-dates"
sys.path.insert(0, str(FEATURE))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, FEATURE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dr = _load("date_refs", "date_refs.py")
mw = _load("markdown_window36", "markdown_window.py")
rc = _load("run_cloud36", "run_cloud.py")
te = _load("task_echo36", "task_echo.py")
T = datetime(2026, 9, 22, 4, tzinfo=timezone.utc)


def test_documented_examples():
    assert dr.resolve("{{(job.start_time - 6.hours).iso_date}}", T) == "2026-09-21"
    assert dr.resolve("{{(job.start_time + 1.week).iso_date}}", T) == "2026-09-29"
    assert dr.resolve("{{(job.start_time - 1.month).month}}", T) == "8"
    assert dr.resolve("{{(job.start_time - 1.day).timestamp_ms}}", T) == "1789963200000"
    assert dr.resolve("{{job.trigger.time.iso_date}}", T) == "2026-09-22"
    assert dr.resolve("{{ (job.trigger.time-1.minute).iso_datetime }}", T) == "2026-09-22T03:59:00.000Z"


def test_edge_cases_and_limits():
    assert all(e["got"] == e["want"] for e in mw.edge_results())
    for bad in ("{{job.start_time - 1.day.iso_date}}", "{{(job.start_time - 1.fortnight).iso_date}}",
                "{{(job.start_time - 1.day).weekday}}", "{{(job.end_time - 1.day).iso_date}}",
                "{{(job.start_time - 2001.years).iso_date}}", "{{(job.start_time + 8000.years).iso_date}}"):
        with pytest.raises(dr.RefError):
            dr.resolve(bad, T)
    assert dr.resolve("{{(job.start_time - 2000.years).year}}", T) == "26"
    assert dr.from_ms(dr.resolve("{{job.start_time.timestamp_ms}}", T)) == T


def test_markdown_plan_and_result_json():
    params = dr.resolve_all(mw.LOCAL_START)
    assert (params["yesterday"], params["week_start"], params["sell_by_cutoff"]) == \
        ("2026-11-08", "2026-11-02", "2026-11-12")
    rows, units = mw.plan(params)
    s = mw.summarise(params, rows, units, mw.edge_results())
    assert s["actions"] == {"pull": 2, "50% off": 5, "25% off": 3, "keep": 3}
    assert all(r["sell_by"] <= "2026-11-12" for r in rows)
    assert {r["action"] for r in rows if r["days_left"] < 0} == {"pull"}
    result = json.loads((FEATURE / "result.json").read_text())
    assert {k: result[k] for k in s} == json.loads(json.dumps(s))
    with pytest.raises(ValueError):
        mw.bind("SELECT :run_date", {"run_date": "2026-11-09'; DROP TABLE x; --"})


def test_task_echo(tmp_path, capsys):
    v = te.main([str(tmp_path), "run_date=2026-11-09", "odd=a=b"])
    assert v == {"run_date": "2026-11-09", "odd": "a=b"}
    assert json.loads((tmp_path / "params.json").read_text()) == v
    assert "DAY36_PARAMS" in capsys.readouterr().out


START_MS = 1794222007123


class Fake(BaseHTTPRequestHandler):
    mode = "ok"
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, status, body=None):
        raw = json.dumps(body if body is not None else {}).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else None

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        self.seen.append(("GET", p))
        if p.endswith("current-metastore-assignment"):
            return self._send(200, {"default_catalog_name": "workspace"})
        if p.endswith("/Me"):
            return self._send(200, {"userName": "someone@example.com"})
        if p.endswith("/runs/get"):
            return self._send(200, {"state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"},
                                    "start_time": START_MS - 900, "end_time": START_MS + 30000,
                                    "tasks": [{"run_id": 7}]})
        if p.endswith("/runs/get-output"):
            params = Fake.job["tasks"][0]["spark_python_task"]["parameters"][1:]
            start = dr.from_ms(START_MS)
            vals = {}
            for pair in params:
                k, ref = pair.split("=", 1)
                vals[k] = ref if self.mode == "unresolved" else dr.resolve(ref, start)
            if self.mode == "wrong":
                vals["last_month"] = "9"
            return self._send(200, {"logs": "hello\nDAY36_PARAMS " + json.dumps(vals)})
        if p.startswith("/api/2.0/fs/files"):
            return self._send(404, {"error_code": "NOT_FOUND"})
        self._send(404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        body = self._body()
        self.seen.append(("POST", p))
        if p.endswith("/jobs/create"):
            if self.mode == "inactive":
                return self._send(403, {"error_code": "PERMISSION_DENIED",
                                        "message": "Organization 1234567890 has been cancelled or is not active yet."})
            Fake.job = body
            return self._send(200, {"job_id": 42})
        if p.endswith("/jobs/run-now"):
            return self._send(200, {"run_id": 99})
        self._send(200, {})

    def do_DELETE(self):
        self.seen.append(("DELETE", urllib.parse.urlparse(self.path).path))
        self._send(200, {})


def _run(monkeypatch, mode):
    Fake.mode, Fake.seen = mode, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(rc, "SCHEME", "http")
    monkeypatch.setattr(rc, "POLL_SECONDS", 0)
    monkeypatch.setenv("DATABRICKS_HOST", f"127.0.0.1:{server.server_port}")
    monkeypatch.setenv("DATABRICKS_TOKEN", "t")
    try:
        return rc.main()
    finally:
        server.shutdown()


@pytest.mark.parametrize("mode,code,annotation", [("ok", 0, "::notice"), ("wrong", 1, "::error"),
                                                   ("unresolved", 0, "::warning"), ("inactive", 0, "::warning")])
def test_cloud_runner(monkeypatch, capsys, mode, code, annotation):
    assert _run(monkeypatch, mode) == code
    out = capsys.readouterr().out
    assert annotation in out and "someone@example.com" not in out and "1234567890" not in out
    if mode == "ok":
        assert "all 12 date-arithmetic references matched" in out and "900 ms after" in out
    if mode != "inactive":
        assert ("POST", "/api/2.2/jobs/delete") in Fake.seen
    assert ("DELETE", "/api/2.1/unity-catalog/schemas/workspace.cmf_day36") in Fake.seen
