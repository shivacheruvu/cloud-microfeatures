"""Day 52 Databricks: AUTO CDC FROM SNAPSHOT (SCD type 2) over nightly inventory snapshots. The Python diff and the
DuckDB reference must agree after each round, the pipeline SQL must use the snapshot-CDC clauses, and the cloud
comparison / refusal handling must behave. No credentials needed."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-52-inventory-snapshot-cdc"
spec = importlib.util.spec_from_file_location("snapshot_cdc52", FEATURE / "snapshot_cdc.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_python_and_duckdb_agree_every_round():
    states = m.run_local()
    assert [len(s) for s in states] == [7, 10]


def test_history_semantics():
    final = m.reference(1)
    assert ("pie_crust", 60, 1, 4) in final                       # vanished from v4's export: closed as a delete
    assert not any(r[0] == "pie_crust" and r[3] is None for r in final)
    assert [r for r in final if r[0] == "stuffing"] == [("stuffing", 55, 2, None), ("stuffing", 80, 1, 2)]
    assert ("whipped", 90, 2, 4) in final                         # new SKU in v2 starts at 2
    assert ("cranberry", 120, 1, 3) in final                      # unchanged at v2, changed at v3
    assert sum(1 for r in final if r[3] is None) == 4


def test_one_update_processes_two_versions_in_order():
    # round 1 lands v1 and v2 together; the history must show both, not just the newest
    assert ("turkey", 40, 1, 2) in m.reference(0) and ("turkey", 25, 2, None) in m.reference(0)


def test_pipeline_sql_uses_snapshot_cdc():
    q = m.pipeline_sql("cat.sch.inventory_snapshots")
    for clause in ("FLOW AUTO CDC", "FROM SNAPSHOT (", "WITH VERSION (", "current_snapshot_version()",
                   "last_snapshot_version()", "KEYS (sku)", "STORED AS SCD TYPE 2"):
        assert clause in q, clause
    assert "{source}" not in q and "SEQUENCE BY" not in q      # snapshot CDC does not take SEQUENCE BY
    select = q[q.index("FROM SNAPSHOT ("):q.index("WITH VERSION (")]
    assert "SELECT sku, on_hand" in select and "SELECT snapshot_version" not in select


def test_compare():
    local = m.run_local()
    assert m.compare([list(s) for s in local], local) == []
    broken = [list(local[0]), [r for r in local[1] if r[0] != "pie_crust"]]
    errs = m.compare(broken, local)
    assert len(errs) == 1 and "round 2" in errs[0] and "pie_crust" in errs[0]
    assert m.compare(local[:1], local) == ["1 rounds read, 2 expected"]


def test_refusals_are_classified():
    assert m.compute_refused("FEATURE_DISABLED: jobs are not enabled")
    assert m.compute_refused("The request could not be processed by the warehouse")
    assert m.beta_unavailable("[PARSE_SYNTAX_ERROR] near 'FROM SNAPSHOT'")
    assert not m.beta_unavailable("TABLE_OR_VIEW_NOT_FOUND")


def test_redaction(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-abc.cloud.databricks.com")
    out = m._redact("at dbc-abc.cloud.databricks.com by a.b@example.com pipeline 12345678")
    assert "dbc-abc" not in out and "@" not in out and "12345678" not in out


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    s = m.summarise(m.run_local())
    assert result["history"] == s["history"] and result["headline"] == s["headline"]
