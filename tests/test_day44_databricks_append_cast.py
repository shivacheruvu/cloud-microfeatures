"""Day 44 Databricks: appending narrower-typed store batches with writeTo().append() and saveAsTable append. The
pure-Python reference and result.json, local open-source Spark 4.3 (parquet: saveAsTable append casts, writeTo()
refuses a v1 table), and the cloud runner (serverless job, compare, warning on an inactive workspace, cleanup) against
a fake REST API."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-44-scan-batch-append-cast"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day44_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ac = _load("append_cast")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(ac.reference()))


def test_headline_numbers():
    assert RESULT["rows"] == 10 and RESULT["revenue_total"] == "74.07"
    assert RESULT["by_store"] == {"3": {"scans": 3, "units": 9, "revenue": "13.21"},
                                  "7": {"scans": 4, "units": 7, "revenue": "34.33"},
                                  "9": {"scans": 3, "units": 7, "revenue": "26.53"}}
    assert RESULT["schema_after"] == RESULT["schema_before"] == ac.SPARK_TYPES
    # every batch column really is narrower than (or equal to) the table's
    assert "SMALLINT" in ac.STORE7["ddl"] and "qty INT" in ac.STORE7["ddl"] and "DECIMAL(5,2)" in ac.STORE9["ddl"]


def test_create_sql():
    c = ac.create_sql("w.s.store_scans")
    assert c[0] == ("CREATE TABLE w.s.store_scans (store_id INT, sku STRING, qty BIGINT, unit_price DECIMAL(10,2), "
                    "scan_date DATE)")
    assert c[1].count("DATE'2026-11-16'") == 3


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    pyspark_sql = pytest.importorskip("pyspark.sql")
    s = (pyspark_sql.SparkSession.builder.master("local[1]").appName("day44")
         .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "1")
         .config("spark.sql.warehouse.dir", str(tmp_path_factory.mktemp("wh"))).getOrCreate())
    yield s
    s.stop()


def test_batches_have_the_narrow_types(spark):
    t = {f.name: f.dataType.simpleString() for f in ac._batch(spark, ac.STORE7).schema.fields}
    assert t == {"store_id": "smallint", "sku": "string", "qty": "int", "unit_price": "decimal(6,2)",
                 "scan_date": "date"}
    assert ac._batch(spark, ac.STORE9).schema["unit_price"].dataType.simpleString() == "decimal(5,2)"


def test_open_source_spark(spark):
    out = ac.run_oss_spark(spark)
    assert out["schema_before"] == out["schema_after"] == ac.SPARK_TYPES
    assert out["appends"]["store_9"]["ok"] is True          # v1 saveAsTable append casts by store assignment
    assert out["appends"]["store_7"]["ok"] is False         # writeTo() needs a v2 table (Delta / Unity Catalog)
    assert "v1 table" in out["appends"]["store_7"]["error"]
    ref = ac.reference()
    assert out["rows_after"] == sorted(r for r in ref["rows_after"] if r[0] != 7)
    assert out["by_store"]["9"] == ref["by_store"]["9"] and "7" not in out["by_store"]


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day44/append_cast.py", "/Volumes/w/s/v/run", "w")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert task["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/run", "w", "cmf_day44"]
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]


def _cloud_copy(**extra):
    return json.loads(json.dumps({**RESULT, "engine": "appends on a serverless job",
                                  "operations": ["CREATE TABLE", "WRITE", "WRITE", "WRITE"], **extra}))


def test_compare_flags_real_differences():
    assert rc.compare(_cloud_copy(), RESULT) == []
    cloud = _cloud_copy()
    cloud["appends"]["store_7"] = {"method": "writeTo().append()", "ok": False,
                                   "error": "AnalysisException: [DELTA_FAILED_TO_MERGE_FIELDS]"}
    cloud["rows"] = 6
    errors = rc.compare(cloud, RESULT)
    assert "DELTA_FAILED_TO_MERGE_FIELDS" in errors[0] and errors[1].startswith("rows")
    widened = _cloud_copy(schema_after={**RESULT["schema_after"], "unit_price": "decimal(11,2)"})
    assert rc.compare(widened, RESULT)[0].startswith("schema_after")


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
    assert "matched local" in out and "schema unchanged" in out and "revenue $74.07" in out
    assert "job took 30s" in out and "someone" not in out and "::warning" not in out
    assert _deleted(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day44"]


def test_failed_append_is_an_error(monkeypatch, capsys):
    cloud = _cloud_copy()
    cloud["appends"]["store_9"]["ok"] = False
    _wire(monkeypatch, FakeApi(cloud))
    assert rc.main() == 1
    assert "::error title=Databricks day 44 differs from local::store_9 append" in capsys.readouterr().out


def test_inactive_workspace_warns_and_cleans_up(monkeypatch, capsys):
    fake = FakeApi(_cloud_copy(), refuse=(403, b'{"error_code": "PERMISSION_DENIED", "message": "Organization 1234567890 has been cancelled or is not active yet."}'))
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "::warning title=Databricks day 44::" in out and "1234567890" not in out
    assert _deleted(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day44"]
