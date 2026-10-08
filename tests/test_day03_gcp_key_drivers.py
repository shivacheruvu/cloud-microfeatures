"""Day 3 Google Cloud: the local AI.KEY_DRIVERS reference agrees with plain Python, and the BigQuery SQL is sound."""
import importlib.util
import itertools
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-03-key-drivers"
spec = importlib.util.spec_from_file_location("key_drivers", FEATURE / "key_drivers.py")
kd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kd)


def python_reference(rows):
    total = {w: sum(int(r["units"]) for r in rows if r["week"] == w) for w in ("this_week", "last_week")}
    out = {}
    for n in range(4):
        for dims in itertools.combinations(kd.DIMS, n):
            for values in {tuple(r[d] for d in dims) for r in rows}:
                seg = [r for r in rows if tuple(r[d] for d in dims) == values]
                i = sum(int(r["units"]) for r in seg if r["week"] == "this_week")
                ref = sum(int(r["units"]) for r in seg if r["week"] == "last_week")
                key = kd.segment_key([f"{d}={v}" for d, v in zip(dims, values)])
                out[key] = (i, ref, i - ref, round(max(i / total["this_week"], ref / total["last_week"]), kd.DIGITS))
    return out


def test_every_segment_matches_python():
    local = kd.run_local(kd.load_rows())
    ref = python_reference(kd.load_rows())
    assert len(local) == len(ref) == 4 * 6 * 3
    for k, (i, r, d, s) in ref.items():
        got = local[k]
        assert (got["metric_interest"], got["metric_reference"], got["difference"]) == (i, r, d)
        assert abs(got["apriori_support"] - s) < 1e-9 and got["contribution"] == abs(d)


def test_segment_key_reads_both_driver_formats():
    assert kd.segment_key(["store=north", "category=produce"]) == "category=produce & store=north"
    assert kd.segment_key("[store=north,category=produce]") == "category=produce & store=north"
    assert kd.segment_key(["all"]) == kd.segment_key("[all]") == kd.segment_key([]) == "all"


def test_planted_drivers_come_out_on_top():
    s = kd.summarise(kd.run_local(kd.load_rows()))
    assert [d["segment"] for d in s["top_drivers"]] == ["category=produce & store=north", "category=bakery & channel=online"]


def test_bigquery_sql_inlines_all_rows_and_uses_key_drivers():
    rows = kd.load_rows()
    sql = kd.bigquery_sql(rows)
    assert "FROM AI.KEY_DRIVERS(" in sql and "interest_label_col => 'is_this_week'" in sql
    assert "enable_pruning => FALSE" in sql and "{values}" not in sql
    assert sql.count("'this_week'") == sum(r["week"] == "this_week" for r in rows) + 1


def test_compare_accepts_matching_bigquery_output(tmp_path, capsys):
    local = kd.run_local(kd.load_rows())
    fake = [{"drivers": ["all"] if k == "all" else k.split(" & "), **{f: str(v[f]) for f in kd.EXACT},
             "unexpected_difference": v["unexpected_difference"]} for k, v in local.items()]
    path = tmp_path / "bq.json"
    path.write_text("Warning: scopes\n" + json.dumps(fake))
    assert kd.read_bq(str(path)).keys() == local.keys()


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    assert {k: result[k] for k in ("segments", "units_this_week", "top_drivers")} == \
        {k: kd.summarise(kd.run_local(kd.load_rows()))[k] for k in ("segments", "units_this_week", "top_drivers")}
