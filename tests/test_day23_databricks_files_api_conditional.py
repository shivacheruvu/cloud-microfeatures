"""Day 23 Databricks: the shared price-file scenario keeps both stores' edits by rejecting stale writes, and the
Files API 3.0 client sends the documented conditional headers: checked by playing the scenario over HTTP against a
local server that enforces If-Match / If-None-Match the way the API reference describes."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-23-price-file-conditional-writes"
spec = importlib.util.spec_from_file_location("price_file", FEATURE / "price_file.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)
PATH = "/Volumes/workspace/microfeatures_day23/price_files/shelf_prices.csv"


def test_local_scenario_rejects_stale_writes_and_keeps_both_edits():
    run = pf.scenario(pf.LocalVolume(), PATH)
    assert [s["outcome"] for s in run["steps"]] == ["ok", "rejected", "ok", "ok", "rejected", "ok", "rejected", "ok"]
    s = pf.summarise(run)
    assert s["final_prices"]["P01"] == "2.99" and s["final_prices"]["P02"] == "4.49"
    assert s["edits_kept"] == ["east", "north"] and s["last_writer_wins_would_lose"] == ["east"]
    assert s["changed_skus"] == ["P01", "P02"]


def test_apply_edit_changes_one_price_only():
    out = pf.prices(pf.apply_edit(pf.seed(), "P04", "0.49"))
    assert out == {**pf.prices(pf.seed()), "P04": "0.49"}
    with pytest.raises(ValueError):
        pf.apply_edit(pf.seed(), "P99", "1.00")


def test_outcomes():
    assert [pf.outcome(c) for c in (200, 204, 409, 412, 500)] == ["ok", "ok", "rejected", "rejected", "error 500"]


class FakeFilesV3(BaseHTTPRequestHandler):
    vol = None
    seen = []

    def log_message(self, *a):
        pass

    def _file(self):
        assert self.path.startswith("/api/3.0/fs/files/Volumes/")
        assert self.headers["Authorization"] == "Bearer test-token"
        return self.path.removeprefix("/api/3.0/fs/files")

    def _send(self, status, body=b"", etag=None):
        self.send_response(status)
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        path = self._file()
        data = self.rfile.read(int(self.headers["Content-Length"]))
        self.seen.append(("PUT", self.headers.get("If-Match"), self.headers.get("If-None-Match")))
        status, etag = self.vol.put(path, data, self.headers.get("If-Match"), self.headers.get("If-None-Match"))
        self._send(status, b"" if status == 200 else b'{"error_code":"PRECONDITION_FAILED"}', etag)

    def do_GET(self):
        status, data, etag = self.vol.get(self._file())
        self._send(status, data or b'{"error_code":"NOT_FOUND"}', etag)

    def do_DELETE(self):
        self.seen.append(("DELETE", self.headers.get("If-Match"), None))
        self._send(self.vol.delete(self._file(), self.headers.get("If-Match")))


def test_files_v3_client_over_http_matches_local(monkeypatch):
    FakeFilesV3.vol, FakeFilesV3.seen = pf.LocalVolume(), []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeFilesV3)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(pf, "SCHEME", "http")
        monkeypatch.setenv("DATABRICKS_TOKEN", "test-token")
        client = pf.FilesV3(f"127.0.0.1:{server.server_port}")
        assert not pf._endpoint_missing(client, PATH)  # plain file-not-found: the endpoint is there
        assert pf.scenario(client, PATH) == pf.scenario(pf.LocalVolume(), PATH)
    finally:
        server.shutdown()
    puts = [s for s in FakeFilesV3.seen if s[0] == "PUT"]
    assert [p[2] for p in puts[:2]] == ["*", "*"] and all(p[1].startswith('"') for p in puts[2:])
    assert all(d[1] for d in FakeFilesV3.seen if d[0] == "DELETE")


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    summary = pf.summarise(pf.scenario(pf.LocalVolume(), PATH))
    assert {k: result[k] for k in summary} == summary
