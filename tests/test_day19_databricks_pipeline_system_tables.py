"""Day 19 Databricks: report.sql (written for system.lakeflow) runs in DuckDB over a synthetic copy of the two
pipeline system tables and matches a plain-Python model: latest SCD2 name, delete rows without a name, an update split
over an hour boundary, the 7-day window and the name filter. The cloud checks accept a sound report and reject broken
ones (no cloud)."""
import importlib.util
from datetime import timedelta
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-19-pipeline-run-report"
spec = importlib.util.spec_from_file_location("run_report_day19", FEATURE / "run_report.py")
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)

LOCAL = rr.run_local()


def test_sql_reads_the_new_system_tables_with_markers():
    sql = rr.report_sql()
    assert "system.lakeflow.pipelines" in sql and "system.lakeflow.pipeline_update_timeline" in sql
    assert ":since" in sql and ":name_like" in sql
    duck = rr.to_duckdb(sql, rr.SINCE, rr.NAME_LIKE)
    assert "system." not in duck and ":since" not in duck and "timestampdiff" not in duck


def test_local_matches_pure_python():
    assert LOCAL == rr.pure_python()


def test_report_rows():
    rows = {r[0]: dict(zip(rr.COLUMNS, r)) for r in LOCAL}
    assert set(rows) == {rr.EXPECTED, "microfeatures-dev-store-params"}       # renamed in; others filtered out
    d18 = rows[rr.EXPECTED]
    assert (d18["pipeline_ids"], d18["deleted_ids"], d18["updates"], d18["completed"]) == (4, 4, 8, 8)
    dev = rows["microfeatures-dev-store-params"]
    assert (dev["failed"], dev["running"], dev["max_seconds"], dev["last_result"]) == (1, 1, 3900, "RUNNING")


def test_window_moves():
    later = rr.run_local(since=rr.SINCE + timedelta(days=5))                 # from 10-21 15:00: only 10-22/23 runs
    assert [r[0] for r in later] == [rr.EXPECTED, "microfeatures-dev-store-params"]
    assert dict(zip(rr.COLUMNS, later[1]))["updates"] == 1
    assert rr.run_local(since=rr.NOW) == []


def test_checks():
    assert rr.check(LOCAL) == []
    assert rr.check([r for r in LOCAL if r[0] != rr.EXPECTED])                # day 18 missing
    bad = list(LOCAL[0]); bad[4] -= 1                                         # outcomes don't add up
    assert rr.check([tuple(bad)] + LOCAL[1:])
    assert rr.compare_schemas(LOCAL, [list(r) for r in LOCAL]) == []
    assert rr.compare_schemas(LOCAL, None) == []
    assert rr.compare_schemas(LOCAL, LOCAL[:1])


def test_result_json_is_current():
    import json
    saved = json.loads((FEATURE / "result.json").read_text())
    assert saved["headline"] == rr.summarise(LOCAL)["headline"]
