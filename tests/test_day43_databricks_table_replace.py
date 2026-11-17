"""Day 43 Databricks: CREATE OR REPLACE TABLE ... AS SELECT on a documented table. The pure-Python reference (week 47
rows, unit costs, comments kept), result.json is current, local open-source Spark 4.3 computes the same rows but a
drop + CTAS loses the table and column comments (what a replace did before the 2026-07-06 serverless release), the
Unity Catalog steps are built right, and the cloud runner submits a serverless job, compares, warns on an inactive
workspace and cleans up (fake REST API)."""
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as R

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-43-cost-table-replace"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day43_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tr = _load("table_replace")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(tr.reference()))


def test_headline_numbers():
    assert RESULT["rows"] == 6 and RESULT["columns_after"][-2:] == ["unit_cost", "week"]
    rows = {r[0]: r for r in RESULT["rows_after"]}
    assert rows["HF-2002"][3:] == ["43.08", "3.59", 47] and rows["HF-1002"][3:] == ["13.56", "3.39", 47]
    assert RESULT["changed_skus"] == ["HF-1001", "HF-2002", "HF-3001"] and RESULT["case_cost_total"] == "160.85"
    assert RESULT["cheapest_unit"] == {"sku": "HF-3001", "unit_cost": "2.39"}
    assert RESULT["kept"] == {"table_comment": True, "column_comments": 4}
    assert RESULT["new_column_comments"] == {"unit_cost": None, "week": None}


def test_uc_statements():
    create = tr.create_sql("w.s.supplier_case_costs")
    assert create[0].startswith("CREATE TABLE w.s.supplier_case_costs (sku STRING COMMENT 'Harbor Farms supplier SKU")
    assert "case_cost DECIMAL(8,2) COMMENT 'USD per case, FOB warehouse'" in create[0]
    assert create[0].endswith("COMMENT " + tr._q(tr.TABLE_COMMENT)) and "('HF-1001'" in create[1]
    reload = tr.reload_sql("w.s.supplier_case_costs")
    assert reload.startswith("CREATE OR REPLACE TABLE w.s.supplier_case_costs AS SELECT")
    assert "COMMENT" not in reload and "round(case_cost / units, 2) AS unit_cost, 47 AS week" in reload
    assert "CAST(43.08 AS DECIMAL(8,2))" in reload and "41.88" not in reload


class FakeSpark:
    """Plays Unity Catalog after the 2026-07-06 release: the CTAS replace keeps table and column comments."""

    def __init__(self, keep=True):
        self.keep, self.table_comment, self.comments, self.cols, self.data = keep, None, {}, [], []

    class _Df:
        def __init__(self, rows, cols=None):
            self.rows, self.columns = rows, cols or []

        def collect(self):
            return self.rows

    def sql(self, q):
        if q.startswith("CREATE TABLE"):
            self.table_comment, self.comments = tr.TABLE_COMMENT, dict(tr.COLUMN_COMMENTS)
            self.cols = list(tr.COLUMNS_BEFORE)
        elif q.startswith("INSERT INTO"):
            self.data = [(s, p, u, Decimal(c)) for s, p, u, c in tr.WEEK46]
        elif q.startswith("CREATE OR REPLACE TABLE"):
            self.cols = list(tr.COLUMNS_AFTER)
            self.data = [(s, p, u, Decimal(c), Decimal(tr.unit_cost(c, u)), 47) for s, p, u, c in tr.week47_rows()]
            self.comments = {c: self.comments.get(c) if self.keep else None for c in self.cols}
            if not self.keep:
                self.table_comment = None
        elif "information_schema.tables" in q:
            return self._Df([R(comment=self.table_comment)])
        elif "information_schema.columns" in q:
            return self._Df([R(column_name=c, comment=self.comments.get(c)) for c in self.cols])
        elif q.startswith("SELECT * FROM"):
            return self._Df(list(self.data))
        elif q.startswith("DESCRIBE HISTORY"):
            return self._Df([R(operation="CREATE OR REPLACE TABLE AS SELECT")])
        return self._Df([])

    def table(self, name):
        return self._Df([], list(self.cols))


def test_run_uc_against_fake_unity_catalog():
    out = tr.run_uc(FakeSpark(), "w", "s")
    assert out.pop("operation") == "CREATE OR REPLACE TABLE AS SELECT"
    assert json.loads(json.dumps(out)) == {k: v for k, v in RESULT.items() if k != "engine"}
    lost = tr.run_uc(FakeSpark(keep=False), "w", "s")
    assert lost["kept"] == {"table_comment": False, "column_comments": 0}


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    pyspark_sql = pytest.importorskip("pyspark.sql")
    s = (pyspark_sql.SparkSession.builder.master("local[1]").appName("day43")
         .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "1")
         .config("spark.sql.warehouse.dir", str(tmp_path_factory.mktemp("wh"))).getOrCreate())
    yield s
    s.stop()


def test_open_source_spark_drop_and_ctas_loses_comments(spark):
    out = tr.run_oss_spark(spark)
    ref = tr.reference()
    for k in ("rows_after", "columns_after", "changed_skus", "case_cost_total", "cheapest_unit"):
        assert out[k] == ref[k], k
    assert out["before"] == ref["before"]
    assert out["after"]["table_comment"] is None and set(out["after"]["column_comments"].values()) == {None}
    assert out["kept"] == {"table_comment": False, "column_comments": 0}


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day43/table_replace.py", "/Volumes/w/s/v/run", "w")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert task["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/run", "w", "cmf_day43"]
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]


def _cloud_copy(**extra):
    return json.loads(json.dumps({**RESULT, "engine": "CREATE OR REPLACE TABLE AS SELECT on a serverless job",
                                  "operation": "CREATE OR REPLACE TABLE AS SELECT", **extra}))


def test_compare_flags_real_differences():
    assert rc.compare(_cloud_copy(), RESULT) == []
    cloud = _cloud_copy()
    cloud["after"]["column_comments"]["case_cost"] = None
    cloud["kept"]["column_comments"] = 3
    errors = rc.compare(cloud, RESULT)
    assert len(errors) == 2 and "after" in errors[0] and "kept" in errors[1]


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
    assert "matched local" in out and "4 -> 6 columns" in out and "all 4 column comments" in out
    assert "job took 30s" in out and "someone" not in out and "::warning" not in out
    assert _deleted(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day43"]


def test_commented_new_column_is_a_warning(monkeypatch, capsys):
    _wire(monkeypatch, FakeApi(_cloud_copy(new_column_comments={"unit_cost": "x", "week": None})))
    assert rc.main() == 0
    assert "::warning title=Databricks day 43::new columns came back with comments" in capsys.readouterr().out


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
