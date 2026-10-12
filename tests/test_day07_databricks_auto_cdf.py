"""Day 7 Databricks: the local snapshot diff gives the expected change feed for the four price steps, table_changes()
rows map onto it by commit order, and the cloud comparison accepts a matching feed and rejects a wrong one or legacy
CDF (no cloud)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-07-shelf-tag-changes"
spec = importlib.util.spec_from_file_location("shelf_changes_day07", FEATURE / "shelf_changes.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

BASE, FEED = sc.load("shelf_prices"), sc.load("price_feed")
LOCAL = sc.run_local(BASE, FEED)


def as_table_changes(changes, first_version=3):
    """table_changes-shaped rows (strings, as the Statement Execution API returns them); versions start anywhere."""
    return [[str(first_version + c["step"]), c["type"], c["store"], c["barcode"], c["product"], str(c["price_cents"])]
            for c in changes]


def test_local_change_feed():
    per_step = sc.summarise(LOCAL)["per_step"]
    assert per_step["step1_load"] == {"insert": 12}
    assert per_step["step2_supplier_increase"] == {"update_preimage": 2, "update_postimage": 2}
    assert per_step["step3_discontinue"] == {"delete": 1}
    # the feed's two unchanged rows (north bread, north bananas) produce no change rows
    assert per_step["step4_weekly_feed"] == {"update_preimage": 1, "update_postimage": 1, "insert": 1}
    assert len(LOCAL["final"]) == 12


def test_tags_to_change():
    tags = {(t["store"], t["product"]): (t["was_cents"], t["now_cents"], t["action"])
            for t in sc.summarise(LOCAL)["tags_to_change"]}
    assert tags == {("north", "whole milk 1gal"): (389, 409, "reprint"),
                    ("south", "whole milk 1gal"): (379, 399, "reprint"),
                    ("north", "oat milk 64oz"): (499, None, "remove tag"),
                    ("south", "large eggs 12ct"): (419, 389, "reprint"),
                    ("south", "ginger kombucha 16oz"): (None, 349, "new tag")}


def test_sql_uses_row_tracking_without_cdf_property():
    sql = sc.statements()
    assert "'delta.enableRowTracking' = 'true'" in sql["create"] and "enableChangeDataFeed" not in sql["create"]
    assert "table_changes('microfeatures_day07.shelf_prices', 0)" in sql["changes"]
    assert set(sc.STEPS) <= set(sql)


def test_cloud_versions_map_to_steps():
    assert sc.cloud_changes(as_table_changes(LOCAL["changes"], first_version=7)) == LOCAL["changes"]


def test_compare():
    cloud = {"changes": sc.cloud_changes(as_table_changes(LOCAL["changes"])), "final": LOCAL["final"],
             "cdf_property": None}
    assert sc.compare(cloud, LOCAL) == ([], [])
    # an update reported as delete + insert: same net effect, so only a warning
    alt = [{**c, "type": {"update_preimage": "delete", "update_postimage": "insert"}.get(c["type"], c["type"])}
           for c in LOCAL["changes"]]
    errors, warnings = sc.compare({**cloud, "changes": sc.sort_changes(alt)}, LOCAL)
    assert errors == [] and warnings
    # a wrong price is an error
    wrong = [{**c, "price_cents": c["price_cents"] + 1} if c["type"] == "update_postimage" else c
             for c in LOCAL["changes"]]
    errors, _ = sc.compare({**cloud, "changes": wrong}, LOCAL)
    assert any("net change" in e for e in errors)
    # legacy CDF switched on is an error
    errors, _ = sc.compare({**cloud, "cdf_property": "true"}, LOCAL)
    assert any("legacy" in e for e in errors)


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    summary = sc.summarise(LOCAL, len(FEED))
    assert {k: result[k] for k in summary} == summary
