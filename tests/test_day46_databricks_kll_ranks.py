"""Day 46 Databricks: free-delivery threshold shares from merged daily KLL sketches with kll_sketch_get_rank_bigint.
The pure-Python reference and result.json, the rank check, the same steps on local open-source Spark (KLL functions
since 4.1), and the cloud runner (serverless job, compare, warning on an inactive workspace, cleanup) against a fake
REST API."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-46-delivery-threshold-ranks"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day46_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tr = _load("threshold_ranks")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(tr.reference()))


def test_headline_numbers():
    assert RESULT["raw_rows"] == 10506 and RESULT["daily_sketches"] == 21
    assert RESULT["thresholds_cents"] == [3500, 5000, 7500]
    chain = RESULT["exact"]["chain"]
    assert chain["n"] == 10506 and chain["qualify"] == [0.3299, 0.1767, 0.0696]
    assert sum(RESULT["exact"][s]["n"] for s in ("3", "7", "9")) == chain["n"]
    # store 7 has the biggest baskets, store 9 (small format) the smallest, at every threshold
    for i in range(3):
        assert RESULT["exact"]["7"]["qualify"][i] > RESULT["exact"]["3"]["qualify"][i] > RESULT["exact"]["9"]["qualify"][i]
    for g in RESULT["exact"].values():
        assert all(abs(r + q - 1) < 1e-3 for r, q in zip(g["rank"], g["qualify"]))


def test_exact_rank():
    assert tr.exact_rank([1, 2, 2, 3], 2) == 0.75 and tr.exact_rank([1, 2, 3], 0) == 0.0


def _exact_estimates():
    return {k: {"n": v["n"], "rank": list(v["rank"])} for k, v in RESULT["exact"].items()}


def test_check_accepts_exact_and_flags_bad_estimates():
    assert tr.check(_exact_estimates()) == []
    est = _exact_estimates()
    est["7"]["n"] -= 1
    est["chain"]["rank"][1] = est["chain"]["rank"][1] + 0.05     # off by 5 points
    est["3"]["rank"] = est["3"]["rank"][:2]
    del est["9"]
    errors = tr.check(est)
    assert any(e.startswith("7: sketch n") for e in errors)
    assert any(e.startswith("chain: rank at $50") for e in errors)
    assert "3: 2 ranks for 3 thresholds" in errors
    assert "9: no merged sketch" in errors


def test_sql_uses_the_kll_rank_function():
    assert "kll_sketch_agg_bigint(basket_cents)" in tr.SKETCH_SQL
    w = tr.rank_sql("s")
    assert w.count("kll_merge_agg_bigint(sketch)") == 2
    assert "kll_sketch_get_rank_bigint(m, array(3500L, 5000L, 7500L))" in w
    assert "basket_cents" not in w           # shares come from the sketch table only


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    pyspark_sql = pytest.importorskip("pyspark.sql")
    s = (pyspark_sql.SparkSession.builder.master("local[1]").appName("day46")
         .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "1")
         .config("spark.sql.warehouse.dir", str(tmp_path_factory.mktemp("wh"))).getOrCreate())
    yield s
    s.stop()


def test_open_source_spark_kll_ranks(spark):
    from pyspark.sql import functions as F
    if not hasattr(F, "kll_sketch_get_rank_bigint"):
        pytest.skip("KLL sketch functions need Spark 4.1+")
    out = tr.run_steps(spark, "baskets46", "basket_sketches46")
    assert out["errors"] == []
    assert out["daily_sketches"] == 21 and out["raw_rows"] == RESULT["raw_rows"]
    assert {k: v["n"] for k, v in out["estimates"].items()} == {k: v["n"] for k, v in RESULT["exact"].items()}
    assert rc.compare(json.loads(json.dumps(out)), RESULT) == []


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day46/threshold_ranks.py", "/Volumes/w/s/v/run", "w")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert task["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/run", "w", "cmf_day46"]
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]


def _cloud(**extra):
    est = {k: {"n": v["n"], "counted": v["n"], "rank": list(v["rank"])} for k, v in RESULT["exact"].items()}
    return {"engine": "KLL sketch ranks on a serverless job", "daily_sketches": 21, "sketch_bytes": 44000,
            "raw_rows": RESULT["raw_rows"], "estimates": est, "errors": [], **extra}


def test_compare_flags_real_differences():
    assert rc.compare(_cloud(), RESULT) == []
    assert rc.compare(_cloud(daily_sketches=20), RESULT)[0].startswith("daily_sketches")
    assert rc.compare(_cloud(errors=["boom"]), RESULT) == ["job check: boom"]
    c = _cloud()
    c["estimates"]["3"]["counted"] = 1
    assert "3: merged sketch n" in rc.compare(c, RESULT)[0]


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
    fake = FakeApi(_cloud())
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "::notice title=Databricks day 46::" in out and "21 stored daily sketches" in out
    assert "chain: n=10506, over $35 33.0% (exact 33.0%)" in out
    assert "Job took 30s" in out and "someone" not in out and "::warning" not in out
    assert _deleted(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day46"]


def test_failed_job_is_an_error(monkeypatch, capsys):
    _wire(monkeypatch, FakeApi(_cloud(), job_state="FAILED"))
    assert rc.main() == 1
    assert "::error title=Databricks day 46 failed::" in capsys.readouterr().out


def test_inactive_workspace_warns_and_cleans_up(monkeypatch, capsys):
    fake = FakeApi(_cloud(), refuse=(403, b'{"error_code": "PERMISSION_DENIED", "message": "Organization 1234567890 has been cancelled or is not active yet."}'))
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "::warning title=Databricks day 46::" in out and "1234567890" not in out
    assert _deleted(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day46"]
