"""Day 38 Databricks: a scalar SQL UDF passed into the range TVF builds the night crew's case pick list. The reference
gives the expected cases; result.json is current; the SQL really puts the UDF inside range(); the old way
(explode(sequence)) gives the same list on local open-source Spark; and the cloud runner submits a serverless job,
requires both ways to match, warns on an inactive workspace and cleans up (fake REST API)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-38-restock-cases-udf-tvf"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day38_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rk = _load("restock_cases")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
LOCAL = {k: v for k, v in RESULT.items() if k != "engine"}
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    assert LOCAL == rk.summarize(rk.reference_rows())


def test_headline_numbers():
    assert (RESULT["shelf_lines"], RESULT["cases"], RESULT["units"]) == (8, 15, 162)
    assert RESULT["cases_by_sku"]["MLK-2L"] == 4          # (30 - 7) / 6 = 3.83 -> 4 cases
    assert RESULT["cases_by_sku"]["CRL-500"] == 3         # empty shelf: 36 / 12
    assert RESULT["lines_needing_no_cases"] == ["EGG-12", "OIL-1L"]   # at par, and over par (not negative)
    assert RESULT["cases_by_aisle"] == {"A01": 6, "B07": 3, "B09": 3, "F03": 3}
    assert RESULT["pick_list"][0] == "A01 MLK-2L #1" and len(RESULT["pick_list"]) == 15


def test_cases_to_pick_edges():
    assert rk.cases_to_pick(18, 18, 6) == 0 and rk.cases_to_pick(25, 20, 6) == 0
    assert rk.cases_to_pick(17, 18, 6) == 1 and rk.cases_to_pick(0, 36, 12) == 3


def test_sql_puts_the_udf_inside_range():
    q = rk.tvf_sql()
    assert q.count("FROM range(1, cases_to_pick(") == len(rk.SHELF)
    assert "FROM range(1, cases_to_pick(7, 30, 6) + 1)" in q and q.count("UNION ALL") == len(rk.SHELF) - 1
    assert "explode(sequence(1, n))" in rk.explode_sql() and "range(" not in rk.explode_sql()
    assert "CREATE OR REPLACE TEMPORARY FUNCTION cases_to_pick" in rk.UDF_SQL


@pytest.fixture(scope="module")
def spark():
    pyspark = pytest.importorskip("pyspark.sql")
    s = pyspark.SparkSession.builder.master("local[1]").appName("day38").getOrCreate()
    yield s


def test_old_way_on_local_spark_matches_reference(spark):
    res = rk.spark_result(spark)
    assert res["explode_sequence"] == LOCAL
    # open-source Spark refuses a UDF in range() (NON_FOLDABLE_ARGUMENT); Databricks accepts it. Either is fine here.
    assert res["udf_in_tvf"] == LOCAL or "FOLDABLE" in (res["udf_in_tvf_error"] or "")


def _cloud_copy(**over):
    return json.loads(json.dumps({"engine": "Databricks serverless", "udf_in_tvf": LOCAL, "udf_in_tvf_error": None,
                                  "explode_sequence": LOCAL, **over}))


def test_compare_flags_real_differences():
    assert rc.compare(_cloud_copy(), LOCAL) == []
    bad = json.loads(json.dumps(LOCAL))
    bad["cases"] = 14
    errors = rc.compare(_cloud_copy(udf_in_tvf=bad), LOCAL)
    assert len(errors) == 1 and errors[0].startswith("udf_in_tvf cases")
    errors = rc.compare(_cloud_copy(udf_in_tvf=None, udf_in_tvf_error="[NON_FOLDABLE_ARGUMENT] ..."), LOCAL)
    assert len(errors) == 1 and "refused" in errors[0]


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day38/restock_cases.py", "/Volumes/w/s/v/run")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]


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
                                    "tasks": [{"run_id": 2}], "start_time": 1000, "end_time": 21000}).encode()
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
    assert "matched local" in out and "15 cases" in out and "job took 20s" in out and "someone" not in out
    assert _deleted(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day38"]
    task = fake.submitted["tasks"][0]["spark_python_task"]
    assert task["python_file"].endswith("/cmf-day38/restock_cases.py")


def test_refused_udf_in_tvf_is_an_error(monkeypatch, capsys):
    fake = FakeApi(_cloud_copy(udf_in_tvf=None, udf_in_tvf_error="[NON_FOLDABLE_ARGUMENT] range"))
    _wire(monkeypatch, fake)
    assert rc.main() == 1
    assert "::error" in capsys.readouterr().out and len(_deleted(fake)) == 1


def test_inactive_workspace_is_a_warning_without_ids(monkeypatch, capsys):
    fake = FakeApi({}, refuse=(404, json.dumps({"error_code": "FEATURE_DISABLED", "message": "Triggering new runs "
                                                "for organization 1234567890123 is currently disabled temporarily."}
                                               ).encode()))
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "::warning" in out and "verified locally only" in out and "1234567890123" not in out
    assert len(_deleted(fake)) == 1
