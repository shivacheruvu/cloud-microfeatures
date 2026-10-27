"""Day 22 Google Cloud: the local export audit finds the empty and the missing file, the compare step accepts bq's
JSON shape (INT64 as strings) and rejects a difference, and the SQL lists the bucket with OBJ.LIST."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-22-export-drop-audit"
spec = importlib.util.spec_from_file_location("export_audit", FEATURE / "export_audit.py")
ea = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ea)


def test_audit_flags_empty_and_missing_exports():
    rows = ea.audit(ea.listing())
    assert len(rows) == 9
    bad = {(r["store"], r["export_date"]): r["status"] for r in rows if r["status"] != "ok"}
    assert bad == {("east", "2026-10-24"): "empty", ("south", "2026-10-25"): "missing"}
    assert all(r["bytes"] > 0 for r in rows if r["status"] == "ok")


def test_audit_ignores_files_outside_the_naming_pattern(tmp_path):
    (tmp_path / "north_2026-10-23.csv").write_text("barcode\n1\n")
    (tmp_path / "README.txt").write_text("not an export")
    (tmp_path / "west_2026-10-23.csv").write_text("other store\n")
    rows = ea.audit(ea.listing(tmp_path))
    assert [r["status"] for r in rows].count("ok") == 1 and len(rows) == 9


def test_compare_accepts_bq_json_and_catches_differences():
    local = ea.audit(ea.listing())
    as_bq = [{**r, "bytes": str(r["bytes"])} for r in local]
    assert ea.compare(as_bq, local) == []
    as_bq[0]["status"] = "missing"
    assert len(ea.compare(as_bq, local)) == 1
    assert ea.compare(as_bq[:-1], local)[0].startswith("BigQuery returned 8 rows")
    assert ea.compare([{"store": "east"}], local)[0].startswith("unreadable")


def test_sql_lists_the_bucket_with_obj_list():
    q = ea.sql("mf-day22-1-1730000000-123")
    assert "FROM OBJ.LIST('gs://mf-day22-1-1730000000-123/exports/*')" in q
    assert "GENERATE_DATE_ARRAY(DATE '2026-10-23', DATE '2026-10-25')" in q
    assert "UNNEST(['east', 'north', 'south'])" in q
    with pytest.raises(ValueError):
        ea.sql("x'/*')) UNION ALL SELECT")


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    files = ea.listing()
    summary = ea.summarise(ea.audit(files), files)
    assert {k: result[k] for k in summary} == summary
