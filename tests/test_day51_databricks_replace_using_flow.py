"""Day 51 Databricks: the REPLACE USING flow's expected shelf after each delivery round (pure Python and DuckDB agree),
the delisted SKU and the late old file, the pipeline SQL's shape, and the cloud comparison (no cloud)."""
import importlib.util
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-51-price-files-replace-using"
spec = importlib.util.spec_from_file_location("price_files_day51", FEATURE / "price_files.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)

STATES = pf.run_local()


def prices(state):
    return {(s, k): (v, p) for s, v, k, p in state}


def test_duckdb_matches_python_every_round():
    for i in range(len(pf.ROUNDS)):
        assert pf.duckdb_state(i) == pf.reference(i)
    assert [len(s) for s in STATES] == [14, 14, 14]


def test_corrected_file_replaces_the_whole_store():
    r1, r2 = prices(STATES[0]), prices(STATES[1])
    assert ("S1", "0903") in r1 and ("S1", "0903") not in r2          # recalled SKU delisted by the new file
    assert r2[("S1", "2201")] == (2, 449)                              # price fix
    assert ("S3", "0903") in r2 and r2[("S2", "0702")] == (1, 399)     # S3 adds a SKU; S2 untouched
    # keep-latest per SKU would have kept S1's delisted 0903
    assert ("S1", 1, "0903", 479) in pf.latest_per_sku(1)


def test_late_old_file_is_ignored():
    final = prices(STATES[-1])
    assert all(v == 2 for v, _ in final.values())
    assert final[("S1", "2201")] == (2, 449) and ("S1", "0903") not in final
    assert final[("S2", "0702")] == (2, 349)
    assert ("S1", 1) in pf.ROUNDS[-1]


def test_pipeline_sql_uses_replace_using_flow():
    sql = pf.pipeline_sql("main.cmf_day51_scratch.price_drops")
    assert "CREATE STREAMING TABLE shelf_prices" in sql
    assert "FLOW REPLACE USING (store_id) SEQUENCE BY file_version BY NAME" in sql
    assert "FROM STREAM(main.cmf_day51_scratch.price_drops)" in sql and "{source}" not in sql


def test_compare_and_refusal_classifiers():
    assert pf.compare(STATES, STATES) == []
    bad = [STATES[0], STATES[1], pf.latest_per_sku(2)]
    errors = pf.compare(bad, STATES)
    assert len(errors) == 1 and errors[0].startswith("round 3")
    assert pf.compute_refused("404 FEATURE_DISABLED: Triggering new runs ... disabled temporarily")
    assert pf.compute_refused("The request could not be processed by the warehouse.")
    assert pf.beta_unavailable("REPLACE USING flows are not enabled for this workspace")
    assert not pf.beta_unavailable("TABLE_OR_VIEW_NOT_FOUND")


def test_redact_hides_host_and_emails(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-example.cloud.databricks.com")
    out = pf._redact("dbc-example.cloud.databricks.com someone@example.com 12345678")
    assert "dbc-example" not in out and "@" not in out and "12345678" not in out
