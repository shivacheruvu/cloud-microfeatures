"""Day 41 Databricks: variant_strip_nulls slims 8 null-padded product payloads; local pyspark 4.3 equals the pure-Python
reference (stripped JSON, bytes, completeness, include_arrays=false), result.json is current, and the cloud runner
submits a serverless job, compares, warns on an inactive workspace and cleans up (fake REST API)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-41-product-json-strip-nulls"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day41_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pp = _load("product_payloads")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(pp.reference()))


def test_headline_numbers():
    assert (RESULT["products"], RESULT["null_values_removed"]) == (8, 28)
    assert (RESULT["bytes_raw"], RESULT["bytes_stripped"], RESULT["bytes_saved_pct"]) == (1722, 1382, 19.7)
    assert RESULT["nutriment_completeness_pct"] == 70.0
    assert RESULT["fully_described"] == ["0004", "0008"] and RESULT["no_nutriments"] == ["0003"]
    per = {r["code"]: r for r in RESULT["per_product"]}
    assert per["0002"]["packaging_empty"] and per["0002"]["labels"] == 0 and per["0002"]["label_slots_kept"] == 2
    assert not per["0003"]["has_packaging"] and per["0005"]["labels"] == 2 and per["0005"]["label_slots_kept"] == 4


def test_strip_reference_matches_documented_examples():
    assert pp.strip({"a": 1, "b": None, "c": 3}) == {"a": 1, "c": 3}
    assert pp.strip([1, None, 3]) == [1, 3]
    assert pp.strip({"a": {"b": None, "c": [1, None]}}) == {"a": {"c": [1]}}
    assert pp.strip({"a": [1, None], "b": None}, False) == {"a": [1, None]}
    assert pp.strip({"a": None}) == {} and pp.strip(None) is None


@pytest.fixture(scope="module")
def spark():
    pyspark_sql = pytest.importorskip("pyspark.sql")
    s = (pyspark_sql.SparkSession.builder.master("local[1]").appName("day41")
         .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "1").getOrCreate())
    yield s
    s.stop()


def test_variant_strip_nulls_in_spark_matches_reference(spark):
    assert pp.run_spark(spark) == json.loads(json.dumps(pp.reference()))


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day41/product_payloads.py", "/Volumes/w/s/v/run")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]


def _cloud_copy():
    return json.loads(json.dumps({**RESULT, "engine": "variant_strip_nulls on a Databricks serverless job"}))


def test_compare_flags_real_differences():
    assert rc.compare(_cloud_copy(), RESULT) == []
    cloud = _cloud_copy()
    cloud["bytes_stripped"] = 1500
    cloud["checks"]["stripped_equal_reference"] = False
    errors = rc.compare(cloud, RESULT)
    assert len(errors) == 2 and "bytes_stripped" in errors[0] and "checks" in errors[1]


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
    assert "matched local" in out and "1722 -> 1382" in out and "job took 30s" in out and "someone" not in out
    assert _deleted(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day41"]


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
