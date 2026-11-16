"""Day 42 Databricks: CREATE OR REPLACE VIEW on a governed view. The pure-Python reference (rows, columns, metadata
kept), result.json is current, local open-source Spark 4.3 gives the same rows but drops the comment and properties
(the behaviour Databricks SQL 2026.35 fixes for Unity Catalog views), the Unity Catalog steps are built right, and the
cloud runner submits a serverless job, compares, warns on an inactive workspace and cleans up (fake REST API)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-42-price-view-replace"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day42_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vr = _load("view_replace")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(vr.reference()))


def test_headline_numbers():
    assert RESULT["rows"] == 6 and RESULT["columns_after"][-2:] == ["units", "unit_cost"]
    rows = {r[0]: r for r in RESULT["rows_after"]}
    assert rows["HF-2002"][4] == "3.49" and rows["HF-2001"][4] == "3.90" and rows["HF-3002"][4] == "2.55"
    assert RESULT["cheapest_unit"] == {"sku": "HF-3001", "unit_cost": "2.49"}
    assert RESULT["kept"] == {"comment": True, "tags": True, "grant": True}
    assert RESULT["after"]["grants"] == ["SELECT to account users"]


def test_uc_statements():
    setup = vr.setup_sql("w.s.shelf_costs", "w.s.shelf_cost_v")
    assert setup[0].startswith("CREATE OR REPLACE TABLE w.s.shelf_costs") and "VALUES ('HF-1001'" in setup[1]
    assert setup[2].startswith("CREATE VIEW w.s.shelf_cost_v COMMENT 'Case cost per SKU")
    gov = vr.governance_sql("w.s.shelf_cost_v")
    assert gov == ["ALTER VIEW w.s.shelf_cost_v SET TAGS ('domain' = 'pricing', 'audience' = 'store-dashboards')",
                   "GRANT SELECT ON VIEW w.s.shelf_cost_v TO `account users`"]
    rep = vr.replace_sql("w.s.shelf_costs", "w.s.shelf_cost_v")
    assert rep.startswith("CREATE OR REPLACE VIEW w.s.shelf_cost_v AS SELECT")
    assert "COMMENT" not in rep and "TAGS" not in rep and "round(case_cost / units, 2) AS unit_cost" in rep


class FakeSpark:
    """Plays Unity Catalog on Databricks SQL 2026.35: the replace keeps comment, tags, grants and creation time."""

    def __init__(self, keep=True):
        self.keep, self.sql_log, self.meta, self.cols = keep, [], {}, []

    class _Df:
        def __init__(self, rows, cols=None):
            self.rows, self.columns = rows, cols or []

        def collect(self):
            return self.rows

    def sql(self, q):
        from types import SimpleNamespace as R
        self.sql_log.append(q)
        if q.startswith("CREATE VIEW"):
            self.meta = {"comment": vr.COMMENT, "created": "2026-11-15 10:00:00", "tags": {}, "grants": []}
            self.cols = vr.COLUMNS_BEFORE
        elif q.startswith("ALTER VIEW"):
            self.meta["tags"] = dict(vr.TAGS)
        elif q.startswith("GRANT"):
            self.meta["grants"] = [("SELECT", "account users")]
        elif q.startswith("CREATE OR REPLACE VIEW"):
            self.cols = vr.COLUMNS_AFTER
            if not self.keep:
                self.meta = {"comment": None, "created": "2026-11-15 10:00:05", "tags": {}, "grants": []}
        elif "information_schema.tables" in q:
            return self._Df([R(comment=self.meta["comment"], created=self.meta["created"])])
        elif "information_schema.table_tags" in q:
            return self._Df([R(tag_name=k, tag_value=v) for k, v in self.meta["tags"].items()])
        elif "information_schema.table_privileges" in q:
            return self._Df([R(privilege_type=p, grantee=g) for p, g in self.meta["grants"]])
        elif q.startswith("SELECT * FROM"):
            from decimal import Decimal
            rows = [[s, p, Decimal(c), u, Decimal(vr.unit_cost(c, u))][:len(self.cols)] for s, p, u, c in vr.ROWS]
            if self.cols == vr.COLUMNS_BEFORE:
                rows = [[s, p, Decimal(c)] for s, p, u, c in vr.ROWS]
            return self._Df([tuple(r) for r in rows])
        return self._Df([])

    def table(self, name):
        return self._Df([], list(self.cols))


def test_run_uc_against_fake_unity_catalog():
    out = vr.run_uc(FakeSpark(), "w", "s")
    assert out.pop("identity_kept") is True
    assert json.loads(json.dumps(out)) == {k: v for k, v in RESULT.items() if k != "engine"}
    lost = vr.run_uc(FakeSpark(keep=False), "w", "s")
    assert lost["kept"] == {"comment": False, "tags": False, "grant": False} and lost["identity_kept"] is False


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    pyspark_sql = pytest.importorskip("pyspark.sql")
    s = (pyspark_sql.SparkSession.builder.master("local[1]").appName("day42")
         .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "1")
         .config("spark.sql.warehouse.dir", str(tmp_path_factory.mktemp("wh"))).getOrCreate())
    yield s
    s.stop()


def test_open_source_spark_replace_drops_metadata(spark):
    out = vr.run_oss_spark(spark)
    ref = vr.reference()
    assert out["rows_after"] == ref["rows_after"] and out["columns_after"] == ref["columns_after"]
    assert out["before"]["comment"] == vr.COMMENT and out["before"]["tags"] == vr.TAGS
    assert out["after"]["comment"] is None and out["after"]["tags"] == {}
    assert out["kept"]["comment"] is False and out["kept"]["tags"] is False


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day42/view_replace.py", "/Volumes/w/s/v/run", "w")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert task["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/run", "w", "cmf_day42"]
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]


def _cloud_copy(identity=True):
    return json.loads(json.dumps({**RESULT, "engine": "CREATE OR REPLACE VIEW on a serverless job",
                                  "identity_kept": identity}))


def test_compare_flags_real_differences():
    assert rc.compare(_cloud_copy(), RESULT) == []
    cloud = _cloud_copy()
    cloud["after"]["comment"] = None
    cloud["kept"]["comment"] = False
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
    assert "matched local" in out and "3 -> 5 columns" in out and "creation time unchanged" in out
    assert "job took 30s" in out and "someone" not in out and "::warning" not in out
    assert _deleted(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day42"]


def test_reset_creation_time_is_a_warning(monkeypatch, capsys):
    _wire(monkeypatch, FakeApi(_cloud_copy(identity=False)))
    assert rc.main() == 0
    assert "::warning title=Databricks day 42::information_schema.tables.created changed" in capsys.readouterr().out


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
