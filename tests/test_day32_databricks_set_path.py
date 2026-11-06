"""Day 32 Databricks: the same unqualified basket report gives regular or ad-week totals depending only on SET PATH
(local pyspark 4.3 with spark.sql.path.enabled); missing schemas are skipped, one-part and duplicate entries are
refused, a path without system.session hides temp views; result.json is current; and the cloud runner submits a
serverless job, compares, warns on an inactive workspace and cleans up all three schemas (fake REST API)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-32-price-book-path"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day32_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pp = _load("price_path")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(pp.reference()))


def test_headline_numbers():
    assert (RESULT["baskets"], RESULT["units"]) == (3, 28)
    assert (RESULT["regular_total"], RESULT["ad_week_total"], RESULT["saved"]) == (95.12, 86.2, 8.92)
    assert [b["saved"] for b in RESULT["by_basket"]] == [3.42, 3.58, 1.92]
    assert RESULT["paths"]["ad_week"].startswith("<catalog>.cmf_day32_ad_week,")


def test_ad_week_function_only_changes_deal_items():
    assert pp._line("0004", 3.29, 3, True) == 8.29          # 2 for $5 + one at shelf price
    assert pp._line("0010", 4.59, 2, True) == 7.34          # 20% off
    assert pp._line("0008", 8.99, 1, True) == 7.99          # $1 off
    assert pp._line("0002", 3.89, 2, True) == pp._line("0002", 3.89, 2, False) == 7.78


def test_normalise_path_drops_system_ai_and_catalog():
    p = "`system`.`session`, workspace.cmf_day32_regular, system.builtin, system.ai"
    assert pp.normalise_path(p, "workspace") == "system.session,<catalog>.cmf_day32_regular,system.builtin"


@pytest.fixture(scope="module")
def spark():
    pyspark_sql = pytest.importorskip("pyspark.sql")
    s = (pyspark_sql.SparkSession.builder.master("local[1]").appName("day32")
         .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "1")
         .config("spark.sql.path.enabled", "true").getOrCreate())
    yield s
    s.stop()


def test_set_path_in_spark_matches_reference(spark):
    out = pp.run_spark(spark, "spark_catalog")
    assert out == json.loads(json.dumps(pp.reference()))
    for stmt in pp.drop_sql("spark_catalog"):
        spark.sql(stmt)


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day32/price_path.py", "/Volumes/w/s/v/result.json", "workspace")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]
    assert task["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/result.json", "workspace"]


def _cloud_copy():
    return json.loads(json.dumps({**RESULT, "engine": "SET PATH on a Databricks serverless job (pyspark 4.3.0)"}))


def test_compare_flags_real_differences():
    assert rc.compare(_cloud_copy(), RESULT) == []
    cloud = _cloud_copy()
    cloud["ad_week_total"] = 95.12                      # e.g. the ad-week function didn't shadow the regular one
    cloud["checks"]["missing_schema_skipped"] = False
    errors = rc.compare(cloud, RESULT)
    assert len(errors) == 2 and "ad_week_total" in errors[0] and "path checks" in errors[1]


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
    assert "matched local" in out and "$86.2" in out and "job took 30s" in out and "someone" not in out
    called = [p for _, p in fake.calls]
    assert "/api/2.0/workspace/import" in called and "/api/2.0/workspace/delete" in called
    assert _deleted(fake) == [f"/api/2.1/unity-catalog/schemas/workspace.{s}"
                              for s in ("cmf_day32_ad_week", "cmf_day32_regular", "cmf_day32")]
    assert fake.submitted["tasks"][0]["spark_python_task"]["parameters"][1] == "workspace"


def test_failed_job_is_an_error_and_still_cleans_up(monkeypatch, capsys):
    fake = FakeApi({}, job_state="FAILED")
    _wire(monkeypatch, fake)
    assert rc.main() == 1
    assert "job ended FAILED: boom" in capsys.readouterr().out
    assert len(_deleted(fake)) == 3


def test_inactive_workspace_is_a_warning_without_ids(monkeypatch, capsys):
    fake = FakeApi({}, refuse=(404, json.dumps({"error_code": "FEATURE_DISABLED", "message": "Triggering new runs "
                                                "for organization 1234567890123 is currently disabled temporarily."}
                                               ).encode()))
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "::warning" in out and "verified locally only" in out and "1234567890123" not in out
    assert len(_deleted(fake)) == 3
