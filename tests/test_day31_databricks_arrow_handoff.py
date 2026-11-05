"""Day 31 Databricks: an Arrow-backed pandas scan export (every column a 3-chunk PyArrow ChunkedArray) goes straight
into spark.createDataFrame; the Spark schema, NULLs and lane totals match the pure-Python reference; result.json is
current; and the cloud runner submits a serverless environment-version-6 job, compares, warns on an inactive
workspace and cleans up (fake REST API)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-31-scan-export-arrow-handoff"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day31_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ah = _load("arrow_handoff")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    data = ah.rows()
    chunks = ah.chunk_info(ah.arrow_backed_pandas(ah.export_parquet(data)))
    out = ah.summarise(ah.lanes_reference(data), ah.SCHEMA, chunks)
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(out))


def test_export_reads_back_as_chunked_arrow_columns():
    pdf = ah.arrow_backed_pandas(ah.export_parquet(ah.rows()))
    info = ah.chunk_info(pdf)
    assert {c: v["chunks"] for c, v in info.items()} == {c: 3 for c in pdf.columns}   # one chunk per lane
    assert info["qty"]["dtype"] == "int64[pyarrow]" and info["unit_price"]["dtype"] == "double[pyarrow]"
    assert int(pdf["unit_price"].isna().sum()) == RESULT["unpriced_scans"] == 7


def test_headline_numbers():
    assert RESULT["rows"] == 42 and RESULT["revenue"] == 366.44 and RESULT["loyalty_share"] == 0.6667
    assert [x["unpriced_scans"] for x in RESULT["lanes"]] == [2, 2, 3]
    assert RESULT["unpriced_products"] == ["bulk almonds", "seasonal squash"]


@pytest.fixture(scope="module")
def spark():
    pyspark_sql = pytest.importorskip("pyspark.sql")
    s = (pyspark_sql.SparkSession.builder.master("local[1]").appName("day31")
         .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "1")
         .config("spark.sql.execution.arrow.pyspark.enabled", "true").getOrCreate())
    yield s
    s.stop()


def test_create_dataframe_on_chunked_arrays_matches_reference(spark):
    pdf = ah.arrow_backed_pandas(ah.export_parquet(ah.rows()))
    schema, lanes = ah.lanes_spark(spark, pdf)
    assert schema == ah.SCHEMA == RESULT["spark_schema"]
    assert lanes == RESULT["lanes"]
    nulls = spark.sql("SELECT COUNT_IF(unit_price IS NULL) AS p, COUNT_IF(loyalty_id IS NULL) AS l FROM scans").first()
    assert (nulls.p, nulls.l) == (7, 14)          # NULLs stay NULL (not NaN, not the string 'None')


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day31/arrow_handoff.py", "/Volumes/w/s/v/result.json")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]
    assert task["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/result.json"]


def _cloud_copy():
    return json.loads(json.dumps({**RESULT, "engine": "spark.createDataFrame on Arrow-backed pandas (pyspark 4.3.0)"}))


def test_compare_flags_real_differences():
    assert rc.compare(_cloud_copy(), RESULT) == []
    cloud = _cloud_copy()
    cloud["unpriced_scans"] = 0                      # e.g. NULL prices arriving as NaN
    cloud["pandas_columns"]["sku"]["chunks"] = 1
    errors = rc.compare(cloud, RESULT)
    assert len(errors) == 2 and "unpriced_scans" in errors[0] and "ChunkedArrays" in errors[1]


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
            out = body["tasks"][0]["spark_python_task"]["parameters"][0]
            if self.job_state == "SUCCESS":
                self.files[out] = json.dumps(self.result).encode()
            return 200, b'{"run_id": 1}'
        if "/runs/get-output" in path:
            return 200, json.dumps({"error": "boom" if self.job_state != "SUCCESS" else None}).encode()
        if "/runs/get" in path:
            return 200, json.dumps({"state": {"life_cycle_state": "TERMINATED", "result_state": self.job_state},
                                    "tasks": [{"run_id": 2}], "start_time": 1000, "end_time": 46000}).encode()
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


def test_cloud_run_end_to_end_against_fake_api(monkeypatch, capsys):
    fake = FakeApi(_cloud_copy())
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "matched local" in out and "job took 45s" in out and "7 unpriced" in out and "someone" not in out
    called = [p for _, p in fake.calls]
    assert "/api/2.0/workspace/import" in called and "/api/2.0/workspace/delete" in called
    assert called[-1] == "/api/2.1/unity-catalog/schemas/workspace.cmf_day31"
    assert fake.submitted["environments"][0]["spec"]["environment_version"] == "6"


def test_failed_job_is_an_error_and_still_cleans_up(monkeypatch, capsys):
    fake = FakeApi({}, job_state="FAILED")
    _wire(monkeypatch, fake)
    assert rc.main() == 1
    assert "job ended FAILED: boom" in capsys.readouterr().out
    assert fake.calls[-1][1] == "/api/2.1/unity-catalog/schemas/workspace.cmf_day31"


def test_inactive_workspace_is_a_warning_without_ids(monkeypatch, capsys):
    fake = FakeApi({}, refuse=(404, json.dumps({"error_code": "FEATURE_DISABLED", "message": "Triggering new runs "
                                                "for organization 1234567890123 is currently disabled temporarily."}
                                               ).encode()))
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "::warning" in out and "verified locally only" in out and "1234567890123" not in out
    assert fake.calls[-1][1] == "/api/2.1/unity-catalog/schemas/workspace.cmf_day31"
