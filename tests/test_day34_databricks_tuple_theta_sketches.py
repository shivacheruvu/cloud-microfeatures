"""Day 34 Databricks: tuple sketches of per-store loyalty spend combined with a theta sketch of e-mailed customers
(tuple_intersection_theta_double, tuple_difference_theta_double, tuple_union_theta_double) give the same reach and
spend as plain set arithmetic (local pyspark 4.2+); result.json is current; and the cloud runner submits a serverless
job, compares, warns on an inactive workspace and cleans up (fake REST API)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-34-coupon-reach-tuple-theta"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day34_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cr = _load("coupon_reach")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == cr.reference()


def test_headline_numbers():
    c = RESULT["chain"]
    assert (RESULT["purchases"], RESULT["emailed"]) == (1800, 250)
    assert (c["buyers"], c["reached_buyers"], c["missed_buyers"], c["touched"]) == (569, 189, 380, 630)
    assert (c["spend_cents"], c["reached_spend_cents"], c["missed_spend_cents"]) == (4700140, 1519327, 3180813)


def test_reference_identities():
    for f in [*RESULT["stores"], RESULT["chain"]]:
        assert f["reached_buyers"] + f["missed_buyers"] == f["buyers"]
        assert f["reached_spend_cents"] + f["missed_spend_cents"] == f["spend_cents"]
        assert f["touched"] == f["buyers"] + RESULT["emailed"] - f["reached_buyers"]
        assert f["touched_spend_cents"] == f["spend_cents"]          # e-mail-only keys carry 0.0 in 'sum' mode
    # a customer who shops at two stores is one chain buyer: the merged sketch dedupes keys and sums spend
    assert sum(s["buyers"] for s in RESULT["stores"]) > RESULT["chain"]["buyers"]
    assert sum(s["spend_cents"] for s in RESULT["stores"]) == RESULT["chain"]["spend_cents"]


def test_sketches_stay_exact():
    assert len({c for c, _, _ in cr.purchases()} | set(cr.emailed())) < 2 ** cr.LG_K


@pytest.fixture(scope="module")
def spark():
    pyspark_sql = pytest.importorskip("pyspark.sql")
    s = (pyspark_sql.SparkSession.builder.master("local[1]").appName("day34")
         .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "1").getOrCreate())
    if "tuple_intersection_theta_double" not in {r.function for r in s.sql("SHOW FUNCTIONS").collect()}:
        pytest.skip("pyspark without tuple sketch functions (needs 4.2+)")
    yield s


def test_tuple_theta_functions_in_spark_match_reference(spark):
    assert cr.run_spark(spark) == cr.reference()


def test_sql_uses_the_new_functions():
    sql = cr.STORE_SQL + cr.CHAIN_SQL
    for fn in ("tuple_intersection_theta_double", "tuple_difference_theta_double", "tuple_union_theta_double",
               "tuple_union_agg_double"):
        assert fn + "(" in sql


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day34/coupon_reach.py", "/Volumes/w/s/v/run")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]
    assert task["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/run"]


def _cloud_copy():
    return json.loads(json.dumps({**RESULT, "engine": "tuple x theta sketch functions on a Databricks serverless job"}))


def test_compare_flags_real_differences():
    assert rc.compare(_cloud_copy(), RESULT) == []
    cloud = _cloud_copy()
    cloud["chain"]["reached_buyers"] = 188
    errors = rc.compare(cloud, RESULT)
    assert len(errors) == 1 and errors[0].startswith("chain")


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
    assert "matched local" in out and "reached 189" in out and "job took 30s" in out and "someone" not in out
    called = [p for _, p in fake.calls]
    assert "/api/2.0/workspace/import" in called and "/api/2.0/workspace/delete" in called
    assert _deleted(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day34"]
    assert fake.submitted["tasks"][0]["spark_python_task"]["parameters"] == ["/Volumes/workspace/cmf_day34/scratch/run"]


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
