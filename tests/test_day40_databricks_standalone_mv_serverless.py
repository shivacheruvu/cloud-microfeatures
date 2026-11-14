"""Day 40 Databricks: the store-day rollup reference (cross-checked with DuckDB running the view's own SQL), the
statements the serverless job sends with spark.sql, the cloud-output check (stale read before REFRESH, new rollup
after), and the REST client against a fake API (success, a view that refreshed itself, an inactive workspace).
No credentials."""
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-40-store-day-mv-notebook"
sys.path.insert(0, str(FEATURE))
spec = importlib.util.spec_from_file_location("store_day_mv", FEATURE / "store_day_mv.py")
mv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mv)
sys.modules["store_day_mv"] = mv
spec = importlib.util.spec_from_file_location("run_cloud40", FEATURE / "run_cloud.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

HOST = "https://dbc-123.cloud.databricks.com"
LOCAL = json.loads((FEATURE / "result.json").read_text())


def test_reference_matches_result_json():
    ref = mv.reference()
    assert {k: LOCAL[k] for k in ref} == json.loads(json.dumps(ref))
    assert ref["totals_before"] == {"store_days": 5, "units": 16, "revenue_cents": 5392}
    assert ref["totals_after"] == {"store_days": 7, "units": 25, "revenue_cents": 8599}
    assert ref["store_days_changed_by_refresh"] == ["S01 2026-11-12", "S02 2026-11-11", "S03 2026-11-11"]


def test_view_sql_on_duckdb_equals_reference():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    con.execute("CREATE TABLE r (store VARCHAR, sale_date DATE, receipt VARCHAR, sku VARCHAR, qty INT, "
                "price_cents INT)")
    for batch, want in ((mv.BATCH_1, "before_refresh"), (mv.BATCH_2, "after_refresh")):
        con.executemany("INSERT INTO r VALUES (?, ?, ?, ?, ?, ?)", batch)
        rows = con.execute(f"SELECT store, CAST(sale_date AS VARCHAR), receipts, units, revenue_cents FROM "
                           f"({mv.ROLLUP_SQL.format(table='r')}) ORDER BY 1, 2").fetchall()
        got = [dict(zip(("store", "sale_date", "receipts", "units", "revenue_cents"), r)) for r in rows]
        assert got == LOCAL[want]


def test_statements():
    s = mv.statements("workspace", "cmf_day40")
    assert s["create_view"].startswith("CREATE OR REPLACE MATERIALIZED VIEW `workspace`.`cmf_day40`.`store_day_sales`")
    assert "FROM `workspace`.`cmf_day40`.`till_receipts` GROUP BY" in s["create_view"]
    assert s["refresh_view"] == "REFRESH MATERIALIZED VIEW `workspace`.`cmf_day40`.`store_day_sales`"
    assert "ASYNC" not in s["refresh_view"]  # standalone MVs on serverless general compute refresh synchronously only
    assert s["insert_1"].count("DATE'") == len(mv.BATCH_1) and s["insert_2"].count("DATE'") == len(mv.BATCH_2)
    for bad in (("work space", "x"), ("workspace", "x`; DROP")):
        with pytest.raises(ValueError):
            mv.statements(*bad)
    with pytest.raises(ValueError):
        mv.values_sql([("S01", "2026-11-10", "R1'", "X", 1, 1)])
    with pytest.raises(ValueError):
        mv.values_sql([("S01", "2026-11-10", "R1", "X", "1", 1)])


def _cloud(**over):
    return {"engine": "Databricks serverless general compute (Spark 4.1.0)", "before_refresh": LOCAL["before_refresh"],
            "stale_before_refresh": LOCAL["before_refresh"], "after_refresh": LOCAL["after_refresh"],
            "table_type": "MATERIALIZED_VIEW", "create_seconds": 40.0, "refresh_seconds": 30.0, **over}


def test_compare():
    assert rc.compare(_cloud(), LOCAL) == []
    errs = rc.compare(_cloud(stale_before_refresh=LOCAL["after_refresh"]), LOCAL)
    assert len(errs) == 1 and errs[0].startswith("stale_before_refresh") and "S01 2026-11-12" in errs[0]
    wrong = [dict(r) for r in LOCAL["after_refresh"]]
    wrong[0]["units"] += 1
    assert len(rc.compare(_cloud(after_refresh=wrong), LOCAL)) == 1
    assert len(rc.compare(_cloud(before_refresh=None), LOCAL)) == 1


class FakeApi:
    def __init__(self, result, refuse=None):
        self.calls, self.result, self.files, self.refuse, self.submitted = [], result, {}, refuse, None

    def __call__(self, method, path, data=None, headers=None):
        self.calls.append((method, path.split("?")[0]))
        body = json.loads(data) if data and (headers or {}).get("Content-Type") == "application/json" else {}
        if path.endswith("current-metastore-assignment"):
            return 200, b'{"default_catalog_name": "workspace"}'
        if path.endswith("/scim/v2/Me"):
            return 200, b'{"userName": "someone@example.com"}'
        if path.endswith("/runs/submit"):
            if self.refuse:
                return self.refuse
            self.submitted = body
            out = body["tasks"][0]["spark_python_task"]["parameters"][2] + "/result.json"
            self.files[out] = json.dumps(self.result).encode()
            return 200, b'{"run_id": 1}'
        if "/runs/get-output" in path:
            return 200, b"{}"
        if "/runs/get" in path:
            return 200, json.dumps({"state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"},
                                    "tasks": [{"run_id": 2}], "start_time": 1000, "end_time": 91000}).encode()
        if path.startswith("/api/2.0/fs/files"):
            f = self.files.get(unquote(path[len("/api/2.0/fs/files"):].split("?")[0]))
            return (200, f) if f else (404, b"{}")
        return 200, b"{}"


def _wire(monkeypatch, fake):
    monkeypatch.setattr(rc, "_request", fake)
    monkeypatch.setattr(rc, "POLL_SECONDS", 0)
    monkeypatch.setenv("DATABRICKS_HOST", HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", "x")


def _cleaned(fake):
    return [p for m, p in fake.calls if m == "DELETE" and "/schemas/" in p]


def test_cloud_run_end_to_end_against_fake_api(monkeypatch, capsys):
    fake = FakeApi(_cloud())
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "table type MATERIALIZED_VIEW" in out and "5 store-days, 16 units, revenue 53.92" in out
    assert "7 store-days, 25 units, revenue 85.99" in out and "job took 90s" in out
    assert "someone" not in out and "dbc-123" not in out
    task = fake.submitted["tasks"][0]
    assert task["spark_python_task"]["parameters"] == ["workspace", "cmf_day40", "/Volumes/workspace/cmf_day40/run/run"]
    assert fake.submitted["environments"][0]["spec"]["environment_version"] == "6"
    assert ("POST", "/api/2.0/workspace/import") in fake.calls
    assert _cleaned(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day40"]


def test_view_that_moved_before_refresh_is_an_error(monkeypatch, capsys):
    fake = FakeApi(_cloud(stale_before_refresh=LOCAL["after_refresh"]))
    _wire(monkeypatch, fake)
    assert rc.main() == 1
    assert "::error" in capsys.readouterr().out and len(_cleaned(fake)) == 1


def test_inactive_workspace_is_a_warning_without_ids(monkeypatch, capsys):
    fake = FakeApi({}, refuse=(403, json.dumps({"error_code": "PERMISSION_DENIED", "message": "Organization "
                                                "1234567890123 has been cancelled or is not active yet."}).encode()))
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "::warning" in out and "verified locally only" in out and "1234567890123" not in out
    assert len(_cleaned(fake)) == 1
