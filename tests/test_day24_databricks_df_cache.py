"""Day 24 Databricks: the basket job caches its enriched lines and serves three reports from the cache (pyspark
local, same code as the serverless job), result.json is current, and the cloud runner submits a serverless
environment-version-5 job, reads the result back, compares it and cleans up (against a fake REST API)."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-24-basket-cache"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())


@pytest.fixture(scope="module")
def spark():
    pyspark_sql = pytest.importorskip("pyspark.sql")
    s = (pyspark_sql.SparkSession.builder.master("local[2]").appName("day24")
         .config("spark.sql.shuffle.partitions", "4").config("spark.ui.enabled", "false")
         # bind to loopback: on CI runners a hostname lookup can stall the local driver
         .config("spark.driver.host", "127.0.0.1").config("spark.driver.bindAddress", "127.0.0.1")
         .getOrCreate())
    yield s
    s.stop()


def test_cached_reports_match_result_json(spark):
    bc = _load("basket_cache")
    out = bc.run(spark)
    assert out["cache"]["cached_before"] is False
    assert out["cache"]["cached_after_cache"] is True and out["cache"]["cached_after_unpersist"] is False
    assert out["cache"]["rows_cached"] == RESULT["cache"]["rows_cached"]
    assert out["reports"] == RESULT["reports"]


def test_result_json_is_consistent():
    r = RESULT["reports"]
    assert sum(s["revenue_cents"] for s in r["by_store"]) == sum(c["revenue_cents"] for c in r["by_category"])
    assert sum(s["baskets"] for s in r["by_store"]) == sum(r["basket_sizes"].values())
    assert sum(int(k) * v for k, v in r["basket_sizes"].items()) == RESULT["cache"]["rows_cached"]
    revenue = [c["revenue_cents"] for c in r["by_category"]]
    assert revenue == sorted(revenue, reverse=True)


def test_submit_payload_uses_serverless_environment_5():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day24/basket_cache.py", "/Volumes/w/s/v/result.json")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "5"}}]
    assert task["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/result.json"]


def test_compare_and_log_fallback():
    cloud = json.loads(json.dumps({k: RESULT[k] for k in ("cache", "reports")}))
    assert rc.compare(cloud, RESULT) == []
    assert rc.from_logs("noise\nDAY24_RESULT " + json.dumps(cloud) + "\n") == cloud
    assert rc.from_logs("nothing here") is None
    cloud["cache"]["cached_after_cache"] = False
    cloud["reports"]["by_store"][0]["revenue_cents"] += 1
    errors = rc.compare(cloud, RESULT)
    assert len(errors) == 2 and "is_cached=False" in errors[0] and errors[1].startswith("by_store")


def test_redact_hides_host_and_user(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-123.cloud.databricks.com")
    text = rc.redact("dbc-123.cloud.databricks.com /Users/someone@example.com/cmf-day24 failed for a@b.io; "
                     "runs for organization 1234567890123 are disabled")
    assert "dbc-123" not in text and "someone" not in text and "a@b.io" not in text
    assert "1234567890123" not in text and "organization <id>" in text


class FakeApi:
    def __init__(self, result, job_state="SUCCESS"):
        self.calls, self.result, self.job_state, self.files = [], result, job_state, {}

    def __call__(self, method, path, data=None, headers=None):
        self.calls.append((method, path.split("?")[0]))
        body = json.loads(data) if data else {}
        if path.endswith("current-metastore-assignment"):
            return 200, b'{"default_catalog_name": "workspace"}'
        if path.endswith("/scim/v2/Me"):
            return 200, b'{"userName": "someone@example.com"}'
        if path.endswith("/runs/submit"):
            self.submitted = body
            out = body["tasks"][0]["spark_python_task"]["parameters"][0]
            if self.job_state == "SUCCESS":
                self.files[out] = json.dumps(self.result).encode()
            return 200, b'{"run_id": 1}'
        if "/runs/get-output" in path:
            return 200, json.dumps({"error": "boom" if self.job_state != "SUCCESS" else None}).encode()
        if "/runs/get" in path:
            return 200, json.dumps({"state": {"life_cycle_state": "TERMINATED", "result_state": self.job_state},
                                    "tasks": [{"run_id": 2}], "start_time": 1000, "end_time": 61000}).encode()
        if path.startswith("/api/2.0/fs/files"):
            from urllib.parse import unquote
            f = self.files.get(unquote(path[len("/api/2.0/fs/files"):]))
            return (200, f) if f else (404, b"{}")
        return 200, b"{}"


def test_cloud_run_end_to_end_against_fake_api(monkeypatch, capsys):
    fake = FakeApi({k: RESULT[k] for k in ("cache", "reports")})
    monkeypatch.setattr(rc, "_request", fake)
    monkeypatch.setattr(rc, "POLL_SECONDS", 0)
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-123.cloud.databricks.com")
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "matched local" in out and "job took 60s" in out and "someone" not in out
    called = [p for _, p in fake.calls]
    assert "/api/2.0/workspace/import" in called and "/api/2.0/workspace/delete" in called
    assert called[-1] == "/api/2.1/unity-catalog/schemas/workspace.cmf_day24"
    assert fake.submitted["environments"][0]["spec"]["environment_version"] == "5"


def test_cloud_run_reports_a_failed_job_and_still_cleans_up(monkeypatch, capsys):
    fake = FakeApi({}, job_state="FAILED")
    monkeypatch.setattr(rc, "_request", fake)
    monkeypatch.setattr(rc, "POLL_SECONDS", 0)
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-123.cloud.databricks.com")
    assert rc.main() == 1
    assert "job ended FAILED: boom" in capsys.readouterr().out
    assert fake.calls[-1][1] == "/api/2.1/unity-catalog/schemas/workspace.cmf_day24"


def test_disabled_workspace_is_reported_without_ids(monkeypatch, capsys):
    fake = FakeApi({})
    def disabled(method, path, data=None, headers=None):
        if path.endswith("/runs/submit"):
            return 404, json.dumps({"error_code": "FEATURE_DISABLED", "message": "Triggering new runs for "
                                    "organization 1234567890123 is currently disabled temporarily."}).encode()
        return fake(method, path, data, headers)
    monkeypatch.setattr(rc, "_request", disabled)
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-123.cloud.databricks.com")
    assert rc.main() == 1
    out = capsys.readouterr().out
    assert "FEATURE_DISABLED" in out and "Databricks compute disabled" in out and "1234567890123" not in out
    assert fake.calls[-1][1] == "/api/2.1/unity-catalog/schemas/workspace.cmf_day24"
