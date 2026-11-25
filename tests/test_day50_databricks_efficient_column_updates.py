"""Day 50 Databricks: the catalog backfill reference (pure Python == DuckDB running the same ALTER / UPDATE), the
Databricks statements with the efficient-column-updates properties, and the cloud path with the Statement Execution API
mocked (values, DESCRIBE HISTORY metrics, ECU fallback warning, refused workspace, Beta off, cleanup). No credentials."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-50-catalog-backfill-ecu"
spec = importlib.util.spec_from_file_location("catalog_backfill50", FEATURE / "catalog_backfill.py")
cb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cb)


def test_reference_duckdb_and_result_json():
    ref = cb.reference()
    assert len(ref) == 24 and cb.duckdb_values() == ref
    assert ref["PIE-PMK"] == [10, False]  # nutmeg is not a nut
    assert ref["TRL-MIX"] == [6, True] and ref["BTR-4"] == [1, False] and ref["AMD-MLK"] == [5, True]
    saved = json.loads((FEATURE / "result.json").read_text())
    s = cb.summarise()
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))
    assert s["nut_warnings"] == 8 and s["duckdb_equals_reference"]


def test_statements():
    st = dict(cb.steps("workspace"))
    assert "'delta.enableColumnUpdates-preview' = 'true'" in st["create_catalog_ecu"]
    assert "'delta.columnMapping.mode' = 'id'" in st["create_catalog_ecu"]
    assert "TBLPROPERTIES" not in st["create_catalog_std"]
    assert st["add_catalog_std"].endswith("ADD COLUMNS (ingredient_count INT, nut_warning BOOLEAN)")
    assert st["backfill_catalog_ecu"].startswith("UPDATE workspace.cmf_day50_ecu.catalog_ecu SET ingredient_count")
    rb = dict(cb.rollback("workspace"))
    assert rb["purge"] == "REORG TABLE workspace.cmf_day50_ecu.catalog_ecu APPLY (PURGE)"
    assert cb.sql().count(";\n") == 13 and "O''" not in cb.sql()


HIST_COLS = ["version", "timestamp", "userName", "operation", "operationParameters", "operationMetrics"]


def _hist(removed, added):
    m = json.dumps({"numRemovedFiles": str(removed), "numAddedFiles": str(added), "numAddedBytes": "3000",
                    "numUpdatedRows": "24", "numCopiedRows": "0"})
    return HIST_COLS, [["4", "t", "someone@example.com", "UPDATE", "{}", m],
                       ["3", "t", "someone@example.com", "ADD COLUMNS", "{}", "{}"]]


def _fake(monkeypatch, ecu_metrics=(0, 1), wrong=None, fail_on=None, fail_msg=""):
    ran = []

    def api(method, path, body=None):
        return {"warehouses": [{"id": "w1", "enable_serverless_compute": True}]}

    def statement(wh, text, with_columns=False):
        ran.append(text)
        if fail_on and fail_on in text:
            raise RuntimeError(fail_msg)
        if text.startswith("SELECT current_catalog()"):
            return [["workspace"]]
        if text.startswith("DESCRIBE HISTORY"):
            return _hist(*ecu_metrics) if "catalog_ecu" in text else _hist(2, 1)
        if text.startswith("SHOW TBLPROPERTIES"):
            return [["delta.enableColumnUpdates-preview", "true"], ["delta.columnMapping.mode", "id"]]
        if text.startswith("SELECT sku"):
            rows = [[k, str(v[0]), "true" if v[1] else "false"] for k, v in sorted(cb.reference().items())]
            if wrong and wrong in text:
                rows[0][1] = "99"
            return rows
        return []

    monkeypatch.setattr(cb, "_api", api)
    monkeypatch.setattr(cb, "statement", statement)
    return ran


def test_cloud_pass_ecu_in_effect(monkeypatch, capsys):
    ran = _fake(monkeypatch)
    assert cb.run_databricks() == 0
    out = capsys.readouterr().out
    assert "values equal the local reference on both (and after the ECU rollback: ok)" in out
    assert '"numRemovedFiles": 0' in out and "::warning" not in out and "example.com" not in out
    assert ran[-1] == "DROP SCHEMA IF EXISTS workspace.cmf_day50_ecu CASCADE"
    assert any(t.startswith("REORG TABLE") for t in ran)


def test_cloud_fallback_is_warning(monkeypatch, capsys):
    _fake(monkeypatch, ecu_metrics=(2, 1))
    assert cb.run_databricks() == 0
    out = capsys.readouterr().out
    assert "::warning" in out and "fell back to a standard update" in out


def test_cloud_wrong_values_fail_and_clean_up(monkeypatch, capsys):
    ran = _fake(monkeypatch, wrong="catalog_std")
    assert cb.run_databricks() == 1
    assert "::error title=Databricks day 50 check failed::catalog_std" in capsys.readouterr().out
    assert ran[-1].startswith("DROP SCHEMA")


def test_cloud_refused_and_beta_off_are_warnings(monkeypatch, capsys):
    _fake(monkeypatch, fail_on="current_catalog", fail_msg="FAILED: The request could not be processed by the warehouse")
    assert cb.run_databricks() == 0
    ran = _fake(monkeypatch, fail_on="CREATE OR REPLACE TABLE",
                fail_msg="FAILED: delta.enableColumnUpdates-preview is not supported in this workspace")
    assert cb.run_databricks() == 0
    out = capsys.readouterr().out
    assert out.count("::warning") == 2 and "Previews page" in out and ran[-1].startswith("DROP SCHEMA")
    _fake(monkeypatch, fail_on="ALTER TABLE", fail_msg="FAILED: PARSE_SYNTAX_ERROR")
    assert cb.run_databricks() == 1


def test_rollback_failure_is_reported_not_fatal(monkeypatch, capsys):
    _fake(monkeypatch, fail_on="REORG TABLE", fail_msg="FAILED: REORG not supported")
    assert cb.run_databricks() == 0
    assert "after the ECU rollback: FAILED: REORG not supported" in capsys.readouterr().out


def test_update_metrics_and_redaction():
    assert cb.update_metrics(*_hist(3, 1)) == {"numRemovedFiles": 3, "numAddedFiles": 1, "numAddedBytes": 3000,
                                                "numUpdatedRows": 24, "numCopiedRows": 0}
    assert cb.update_metrics(HIST_COLS, []) == {}
    assert "<user>" in cb._redact("by someone@example.com") and "<id>" in cb._redact("ws 12345678901")
