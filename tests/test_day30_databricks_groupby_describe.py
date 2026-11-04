"""Day 30 Databricks: per-store basket describe() rows (the reference behind pandas-on-Spark
SeriesGroupBy.describe, with Spark's nearest-rank quartiles), coupon and bulk bands, result.json is current, and the
cloud runner submits a serverless environment-version-6 job, compares, warns on an inactive workspace and cleans up
(fake REST API)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-30-basket-bands-describe"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day30_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bd = _load("basket_describe")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    data = bd.rows()
    out = bd.summarise(bd.describe_reference(data), data)
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(out))


def test_reference_matches_pandas_except_quartile_interpolation():
    pd = pytest.importorskip("pandas")
    d = pd.DataFrame(bd.rows()).groupby("store")["basket"].describe()
    ref = bd.describe_reference(bd.rows())
    for s in bd.STORES:
        for k in ("count", "mean", "std", "min", "max"):
            assert d.loc[s, k] == pytest.approx(ref[s][k], abs=1e-9)
        interp = next(x for x in RESULT["stores"] if x["store"] == s)["pandas_interpolated_quartiles"]
        for k in ("25%", "50%", "75%"):
            assert d.loc[s, k] == pytest.approx(interp[k], abs=1e-4)


def test_pandas_on_spark_path_when_available():
    pytest.importorskip("pyspark.pandas")   # needs pandas + pyarrow next to pyspark
    try:
        got = bd.describe_pandas_on_spark(bd.rows())
    except Exception as e:  # noqa: BLE001  PandasNotImplementedError before OSS 4.3, or no pyarrow
        if isinstance(e, ImportError) or type(e).__name__ in ("PandasNotImplementedError", "PySparkImportError"):
            pytest.skip(f"pandas API on Spark groupby()[col].describe() unavailable here: {type(e).__name__}")
        raise
    ref = bd.describe_reference(bd.rows())
    assert all(abs(got[s][k] - ref[s][k]) < 1e-9 for s in bd.STORES for k in bd.STATS)


def test_nearest_rank_returns_observed_values():
    v = [1.0, 2.0, 3.0, 4.0]
    assert [bd.nearest_rank(v, q) for q in (0.25, 0.5, 0.75)] == [1.0, 2.0, 3.0]
    assert [bd.interpolated(v, q) for q in (0.25, 0.5, 0.75)] == [1.75, 2.5, 3.25]


def test_findings():
    st = {s["store"]: s for s in RESULT["stores"]}
    assert RESULT["rows"] == 120 and RESULT["stats"] == bd.STATS
    for s in st.values():
        d = s["describe"]
        assert d["count"] == 40 and d["min"] <= d["25%"] <= d["50%"] <= d["75%"] <= d["max"]
        values = {r["basket"] for r in bd.rows() if r["store"] == s["store"]}
        assert {d["25%"], d["50%"], d["75%"]} <= values      # observed baskets, not interpolated
    assert (st["downtown"]["bulk_baskets"], st["suburb"]["bulk_baskets"], st["campus"]["bulk_baskets"]) == (1, 2, 0)
    assert RESULT["most_skewed"] == "suburb"
    assert st["suburb"]["coupon_threshold"] > st["downtown"]["coupon_threshold"] > st["campus"]["coupon_threshold"]


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day30/basket_describe.py", "/Volumes/w/s/v/result.json")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]
    assert task["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/result.json"]


def _cloud_copy():
    return json.loads(json.dumps({**RESULT, "engine": "pyspark.pandas SeriesGroupBy.describe (pyspark 4.3.0)"}))


def test_compare_tolerates_float_noise_and_flags_real_differences():
    cloud = _cloud_copy()
    cloud["stores"][0]["describe"]["std"] += 1e-6
    assert rc.compare(cloud, RESULT) == []
    cloud["stores"][1]["describe"]["75%"] += 0.5    # e.g. an interpolated quartile instead of an observed basket
    cloud["stores"][2]["bulk_baskets"] = 1
    errors = rc.compare(cloud, RESULT)
    assert len(errors) == 2 and "describe() differs" in errors[0] and "bulk_baskets" in errors[1]


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
    assert "matched local" in out and "job took 45s" in out and "someone" not in out
    called = [p for _, p in fake.calls]
    assert "/api/2.0/workspace/import" in called and "/api/2.0/workspace/delete" in called
    assert called[-1] == "/api/2.1/unity-catalog/schemas/workspace.cmf_day30"
    assert fake.submitted["environments"][0]["spec"]["environment_version"] == "6"


def test_failed_job_is_an_error_and_still_cleans_up(monkeypatch, capsys):
    fake = FakeApi({}, job_state="FAILED")
    _wire(monkeypatch, fake)
    assert rc.main() == 1
    assert "job ended FAILED: boom" in capsys.readouterr().out
    assert fake.calls[-1][1] == "/api/2.1/unity-catalog/schemas/workspace.cmf_day30"


def test_inactive_workspace_is_a_warning_without_ids(monkeypatch, capsys):
    fake = FakeApi({}, refuse=(404, json.dumps({"error_code": "FEATURE_DISABLED", "message": "Triggering new runs "
                                                "for organization 1234567890123 is currently disabled temporarily."}
                                               ).encode()))
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "::warning" in out and "verified locally only" in out and "1234567890123" not in out
    assert fake.calls[-1][1] == "/api/2.1/unity-catalog/schemas/workspace.cmf_day30"
