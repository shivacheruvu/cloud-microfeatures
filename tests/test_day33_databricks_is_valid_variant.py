"""Day 33 Databricks: is_valid_variant splits a scan export with three malformed VARIANT payloads (truncated value,
unknown metadata version, undefined type byte) into 11 valid rows and a quarantine list; a raw extract fails with
MALFORMED_VARIANT while the clean extract equals the pure-Python reference (local pyspark 4.3); result.json is current;
and the cloud runner submits a serverless job, compares, warns on an inactive workspace and cleans up (fake REST API)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-33-scan-payload-validation"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day33_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sp = _load("scan_payloads")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(sp.reference()))


def test_headline_numbers():
    assert (RESULT["events"], RESULT["valid"], RESULT["quarantined"]) == (14, 11, ["E05", "E09", "E12"])
    assert (RESULT["units"], RESULT["revenue"]) == (22, 67.48)
    assert [r["scans"] for r in RESULT["by_lane"]] == [4, 4, 3]
    assert RESULT["checks"]["raw_extract"] == "MALFORMED_VARIANT"


def test_payload_json_is_valid_json():
    assert json.loads(sp.payload_json(3, "0002", 1, "3.89")) == {"lane": 3, "sku": "0002", "qty": 1, "price": 3.89}


def test_corruptions_change_the_bytes():
    m, v = b"\x01\x00\x00", b"\x02\x01\x00\x00\x01\x0c\x01"
    assert sp.corrupt("truncated", m, v) == (m, v[:-3])
    assert sp.corrupt("bad_header", m, v)[0][0] == 2
    assert sp.corrupt("bad_type", m, v)[1][0] == 0xFF
    with pytest.raises(ValueError):
        sp.corrupt("other", m, v)


@pytest.fixture(scope="module")
def spark():
    pyspark_sql = pytest.importorskip("pyspark.sql")
    pytest.importorskip("pyarrow")
    s = (pyspark_sql.SparkSession.builder.master("local[1]").appName("day33")
         .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "1").getOrCreate())
    yield s
    s.stop()


def test_is_valid_variant_in_spark_matches_reference(spark, tmp_path):
    out = sp.run_spark(spark, str(tmp_path))
    assert out == json.loads(json.dumps(sp.reference()))
    assert (tmp_path / "raw/scans.parquet").exists() and any((tmp_path / "clean").glob("*.parquet"))


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day33/scan_payloads.py", "/Volumes/w/s/v/run")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]
    assert task["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/run"]


def _cloud_copy():
    return json.loads(json.dumps({**RESULT, "engine": "is_valid_variant on a Databricks serverless job (pyspark 4.3)"}))


def test_compare_flags_real_differences():
    assert rc.compare(_cloud_copy(), RESULT) == []
    cloud = _cloud_copy()
    cloud["quarantined"] = ["E05", "E12"]                 # e.g. a bad header slipped through
    cloud["checks"]["raw_extract"] = "succeeded"
    errors = rc.compare(cloud, RESULT)
    assert len(errors) == 2 and "quarantined" in errors[0] and "checks" in errors[1]


class FakeApi:
    def __init__(self, result, job_state="SUCCESS", refuse=None):
        self.calls, self.result, self.job_state, self.files, self.refuse = [], result, job_state, {}, refuse

    def __call__(self, method, path, data=None, headers=None):
        self.calls.append((method, path.split("?")[0]))
        body = json.loads(data) if data else {}
        if path.endswith("current-metastore-assignment"):
            return 200, b'{"default_catalog_name": "workspace"}'
        if path.endswith("/scim/v2/Me"):
            return 200, b'{"userName": "someone@example.com"}'
        if path.endswith("/runs/submit"):
            if self.refuse:
                return self.refuse
            self.submitted = body
            out = body["tasks"][0]["spark_python_task"]["parameters"][0] + "/result.json"
            if self.job_state == "SUCCESS":
                self.files[out] = json.dumps(self.result).encode()
            return 200, b'{"run_id": 1}'
        if "/runs/get-output" in path:
            return 200, json.dumps({"error": "boom" if self.job_state != "SUCCESS" else None}).encode()
        if "/runs/get" in path:
            return 200, json.dumps({"state": {"life_cycle_state": "TERMINATED", "result_state": self.job_state},
                                    "tasks": [{"run_id": 2}], "start_time": 1000, "end_time": 31000}).encode()
        if path.startswith("/api/2.0/fs/files"):
            from urllib.parse import unquote
            f = self.files.get(unquote(path[len("/api/2.0/fs/files"):]))
            return (200, f) if f else (404, b"{}")
        return 200, b"{}"


def _wire(monkeypatch, fake):
    monkeypatch.setattr(rc, "_request", fake)
    monkeypatch.setattr(rc, "POLL_SECONDS", 0)
    monkeypatch.setenv("DATABRICKS_HOST", HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", "x")


def _deleted(fake):
    return [p for m, p in fake.calls if m == "DELETE" and "/schemas/" in p]


def test_cloud_run_end_to_end_against_fake_api(monkeypatch, capsys):
    fake = FakeApi(_cloud_copy())
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "matched local" in out and "E05, E09, E12" in out and "job took 30s" in out and "someone" not in out
    called = [p for _, p in fake.calls]
    assert "/api/2.0/workspace/import" in called and "/api/2.0/workspace/delete" in called
    assert _deleted(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day33"]
    assert fake.submitted["tasks"][0]["spark_python_task"]["parameters"] == ["/Volumes/workspace/cmf_day33/scratch/run"]


def test_failed_job_is_an_error_and_still_cleans_up(monkeypatch, capsys):
    fake = FakeApi({}, job_state="FAILED")
    _wire(monkeypatch, fake)
    assert rc.main() == 1
    assert "job ended FAILED: boom" in capsys.readouterr().out
    assert len(_deleted(fake)) == 1


def test_inactive_workspace_is_a_warning_without_ids(monkeypatch, capsys):
    fake = FakeApi({}, refuse=(404, json.dumps({"error_code": "FEATURE_DISABLED", "message": "Triggering new runs "
                                                "for organization 1234567890123 is currently disabled temporarily."}
                                               ).encode()))
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "::warning" in out and "verified locally only" in out and "1234567890123" not in out
    assert len(_deleted(fake)) == 1
