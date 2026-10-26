"""Day 21 Google Cloud: the synthetic shelf-days are deterministic, the naive rule misses what the lifts explain, the
CREATE MODEL pins xgboost_version '2.1', and the cloud check accepts a good model and rejects a naive one."""
import csv
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-21-stockout-xgboost"
spec = importlib.util.spec_from_file_location("stockout", FEATURE / "stockout.py")
so = importlib.util.module_from_spec(spec)
spec.loader.exec_module(so)


def test_data_is_deterministic_and_split():
    a, b = so.generate(), so.generate()
    assert a == b
    assert sum(r["split"] == "train" for r in a) == 600 and sum(r["split"] == "holdout" for r in a) == 200
    assert {r["department"] for r in a} == set(so.DEPARTMENTS)


def test_naive_rule_leaves_room_for_the_model():
    s = so.summarise(so.generate())
    assert s["naive_rule_accuracy"] < s["lift_rule_accuracy"] - 0.15
    assert s["naive_rule_missed_stockouts"] > 20


def test_model_sql_pins_xgboost_2_1():
    q = so.sql("model", "proj.ds")
    assert "model_type = 'BOOSTED_TREE_CLASSIFIER'" in q and "xgboost_version = '2.1'" in q
    assert "WHERE split = 'train'" in q and "data_split_method = 'NO_SPLIT'" in q
    p = so.sql("predict", "proj.ds")
    assert "ML.PREDICT(MODEL `proj.ds.stockout_xgb`" in p and "split = 'holdout'" in p
    assert not q.lstrip().startswith("--") and not p.lstrip().startswith("--")  # bq reads a leading -- as a flag


def test_csv_matches_bq_schema(tmp_path):
    out = tmp_path / "s.csv"
    so.write_csv(so.generate(), str(out))
    with open(out) as f:
        header = next(csv.reader(f))
        first = next(csv.reader(f))
    assert header == [c.split(":")[0] for c in so.SCHEMA.split(",")]
    assert first[header.index("stockout")] in ("true", "false")


def test_check_accepts_good_model_rejects_naive():
    rows = so.generate()
    hold = [r for r in rows if r["split"] == "holdout"]
    good = [{"shelf_day_id": r["shelf_day_id"], "predicted_stockout": so.lift_rule(r)} for r in hold]
    naive = [{"shelf_day_id": str(r["shelf_day_id"]), "predicted_stockout": str(so.naive_rule(r)).lower()}
             for r in hold]
    assert so.check(rows, so.parse_predictions(good))[0]
    assert not so.check(rows, so.parse_predictions(naive))[0]
    assert not so.check(rows, so.parse_predictions(good[:-1]))[0]  # a missing prediction fails


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    s = so.summarise(so.generate())
    assert {k: result[k] for k in s} == s
