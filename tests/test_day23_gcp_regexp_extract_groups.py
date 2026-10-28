"""Day 23 Google Cloud: the two receipt patterns split every packed and weighed line into the right fields, leave
coupons and fees for a person, the cloud-side normaliser accepts array or struct results (with empty or null
optional groups), and the compare step catches a difference."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-23-receipt-line-parser"
spec = importlib.util.spec_from_file_location("receipt_parser", FEATURE / "receipt_parser.py")
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)


def test_packed_and_weighed_lines_parse_into_fields():
    assert rp.groups_local(rp.PACKED, "OAT MILK 64OZ 2 @ 3.49") == ["OAT MILK", "64", "OZ", "2", "3.49"]
    assert rp.groups_local(rp.PACKED, "PEANUT BUTTER 16 OZ 2 @ 3.19") == ["PEANUT BUTTER", "16", "OZ", "2", "3.19"]
    assert rp.groups_local(rp.PACKED, "GROUND COFFEE 1.5LB 1 @ 9.99") == ["GROUND COFFEE", "1.5", "LB", "1", "9.99"]
    assert rp.groups_local(rp.PACKED, "BREAD WHOLE WHEAT 1 @ 3.99") == ["BREAD WHOLE WHEAT", None, None, "1", "3.99"]
    assert rp.groups_local(rp.WEIGHED, "BANANAS 2.31 LB @ 0.59/LB") == ["BANANAS", "2.31", "0.59"]
    assert rp.groups_local(rp.PACKED, "BANANAS 2.31 LB @ 0.59/LB") is None
    assert rp.groups_local(rp.PACKED, "COUPON OAT MILK -1.00") is None
    assert rp.groups_local(rp.WEIGHED, "BAG FEE 0.10") is None


def test_amounts_and_summary():
    lines = rp.load_lines()
    items = rp.parse_local(lines)
    by = {(i["receipt"], i["line_no"]): i for i in items}
    assert by[("r1001", 1)]["amount_cents"] == 698          # 2 x 3.49
    assert by[("r1001", 3)]["amount_cents"] == 136          # 2.31 lb x 0.59 = 1.3629 -> 1.36
    assert by[("r1003", 6)]["amount_cents"] == 630          # 0.42 lb x 14.99 = 6.2958 -> 6.30
    s = rp.summarise(lines, items)
    assert (s["lines"], s["packed"], s["weighed"], s["no_pack_size"]) == (18, 12, 4, 1)
    assert s["unparsed"] == ["COUPON OAT MILK -1.00", "BAG FEE 0.10"]
    assert s["parsed_total_by_receipt"] == {"r1001": 24.96, "r1002": 44.47, "r1003": 28.54}


def test_cloud_result_shapes_normalise_the_same():
    want = ["BREAD WHOLE WHEAT", None, None, "1", "3.99"]
    assert rp.groups_from_json(json.dumps(want)) == want
    assert rp.groups_from_json(json.dumps(["BREAD WHOLE WHEAT", "", "", "1", "3.99"])) == want
    assert rp.groups_from_json(json.dumps({"_field_1": "BREAD WHOLE WHEAT", "_field_2": None, "_field_3": None,
                                           "_field_4": "1", "_field_5": "3.99"})) == want
    for no_match in (None, "null", "[]", json.dumps([None, None, None]), json.dumps({"a": None})):
        assert rp.groups_from_json(no_match) is None
    assert rp.shape_of('{"a": "x"}') == "struct" and rp.shape_of('["x"]') == "array" and rp.shape_of(None) == "null"


def test_compare_accepts_matching_bq_rows_and_rejects_a_difference():
    lines = rp.load_lines()
    local = rp.parse_local(lines)
    rows = [{"receipt": l["receipt"], "line_no": str(l["line_no"]),  # bq's JSON gives INT64 as strings
             "packed_groups": json.dumps(rp.groups_local(rp.PACKED, l["line"])),
             "weighed_groups": json.dumps(rp.groups_local(rp.WEIGHED, l["line"]))} for l in lines]
    assert rp.parse_cloud(rows) == local
    rows[0]["packed_groups"] = json.dumps(["OAT MILK", "64", "OZ", "3", "3.49"])
    assert rp.parse_cloud(rows) != local


def test_sql_inlines_lines_and_both_patterns():
    q = rp.sql(rp.load_lines())
    assert q.count("REGEXP_EXTRACT_GROUPS(line, r'") == 2
    assert rp.PACKED in q and rp.WEIGHED in q
    assert "'OAT MILK 64OZ 2 @ 3.49' AS line" in q and "{lines}" not in q
    assert rp._q("it's") == "'it\\'s'"


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    lines = rp.load_lines()
    summary = rp.summarise(lines, rp.parse_local(lines))
    assert {k: result[k] for k in summary} == summary


def test_nested_result_shapes_unwrap():
    """Fix forward: the first cloud run got a one-element result, so one level of nesting is unwrapped."""
    want = ["OAT MILK", "64", "OZ", "2", "3.49"]
    assert rp.groups_from_json(json.dumps([want])) == want
    assert rp.groups_from_json(json.dumps([{"a": "OAT MILK", "b": "64", "c": "OZ", "d": "2", "e": "3.49"}])) == want
    assert rp.groups_from_json(json.dumps({"groups": want})) == want
    assert rp.groups_from_json(json.dumps({"groups": None})) is None


def test_anonymous_struct_from_bigquery():
    """The shape BigQuery actually returned on 2026-10-27: a struct with anonymous fields (all keys "")."""
    raw = '{"":"OAT MILK","":"64","":"OZ","":"2","":"3.49"}'
    assert rp.groups_from_json(raw) == ["OAT MILK", "64", "OZ", "2", "3.49"]
    assert rp.groups_from_json('{"":"BREAD WHOLE WHEAT","":null,"":null,"":"1","":"3.99"}') == \
        ["BREAD WHOLE WHEAT", None, None, "1", "3.99"]
    assert rp.shape_of(raw) == "struct"
