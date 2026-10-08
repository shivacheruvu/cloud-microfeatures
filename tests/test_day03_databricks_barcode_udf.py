"""Day 3 Databricks: the Python UDF body (run locally in DuckDB) agrees with an independent GS1 check."""
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-03-barcode-udf"
spec = importlib.util.spec_from_file_location("barcode_audit", FEATURE / "barcode_audit.py")
ba = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ba)
gtin_status = ba.udf_handler()


def reference(code):
    """Independent GS1 rule: left-pad to 14 digits, weights 3,1,3,... from the left, sum incl. check digit % 10 == 0."""
    if not (code.isascii() and code.isdigit()):
        return "not_numeric"
    if len(code) not in (8, 12, 13, 14):
        return "bad_length"
    padded = code.zfill(14)
    return "valid" if sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(padded)) % 10 == 0 \
        else "bad_check_digit"


def test_known_codes():
    assert gtin_status("4006381333931") == "valid"      # EAN-13 example from GS1
    assert gtin_status("036000291452") == "valid"       # UPC-A
    assert gtin_status("96385074") == "valid"           # GTIN-8
    assert gtin_status("4006381333932") == "bad_check_digit"
    assert gtin_status("400638133393") == "bad_check_digit"
    assert gtin_status("40063813339") == "bad_length"
    assert gtin_status("40063?1333931") == "not_numeric"
    assert gtin_status(" 4006381333931 ") == "valid"
    assert gtin_status(None) is None


def test_every_single_digit_error_is_caught():
    code = "5449000000996"
    for i in range(len(code)):
        for d in "0123456789":
            if d != code[i]:
                assert gtin_status(code[:i] + d + code[i + 1:]) == "bad_check_digit"


def test_matches_reference_on_all_scans():
    for r in ba.load_rows():
        assert gtin_status(r["barcode"]) == reference(r["barcode"]), r


def test_audit_query_matches_python_counts():
    rows = ba.load_rows()
    by_lane = defaultdict(list)
    for r in rows:
        by_lane[r["lane"]].append(r["barcode"])
    expected = []
    for lane, codes in sorted(by_lane.items()):
        st = [reference(c) for c in codes]
        expected.append({"lane": lane, "scans": len(codes), "valid": st.count("valid"),
                         "bad_check_digit": st.count("bad_check_digit"), "bad_length": st.count("bad_length"),
                         "not_numeric": st.count("not_numeric"),
                         "distinct_bad_codes": len({c for c, s in zip(codes, st) if s != "valid"})})
    assert ba.run_local(rows) == expected


def test_udf_uses_handler_and_environment_6():
    sql = ba.udf_sql()
    assert "LANGUAGE PYTHON" in sql and "HANDLER 'gtin_status_handler'" in sql
    assert "environment_version = '6'" in sql
    assert f"{ba.SCHEMA}.gtin_status(barcode)" in ba.audit_sql(ba.load_rows()[:1])


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    assert result["by_lane"] == ba.run_local(ba.load_rows())
