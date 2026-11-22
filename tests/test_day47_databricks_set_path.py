"""Day 47 Databricks: the SET PATH name-resolution model (order, shadowing, missing schemas skipped, a view keeping
its captured path), DuckDB's search_path agreeing on the plain cases, and the cloud-run comparison."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-47-price-book-path"


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, FEATURE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pp = _load("day47_path_prices_t", "path_prices.py")
rc = _load("day47_run_cloud_t", "run_cloud.py")


def test_reference_totals():
    t = pp.reference()["totals"]
    everyday = 2 * 129 + 249 + 389 + 749 + 6 * 25 + 3 * 85
    promo = 2 * 129 + 199 + 389 + 599 + 6 * 20 + 3 * 60
    assert t == {"live": everyday, "promo_first": promo, "missing_skipped": everyday,
                 "view_captured": promo, "direct_after_view": everyday}


def test_resolution_rules():
    cat = "workspace"
    live, promo = f"{cat}.{pp.LIVE}", f"{cat}.{pp.PROMO}"
    assert pp.resolve([promo, live], "basket", cat) == pp.resolve([live], "basket", cat)  # falls through to live
    assert pp.resolve([promo, live], "shelf_prices", cat)["BANANA"] == 20
    try:
        pp.resolve(pp.SYSTEM + [promo], "basket", cat)
        raise AssertionError("basket must not resolve without live on the path")
    except LookupError as e:
        assert "TABLE_OR_VIEW_NOT_FOUND" in str(e)
    cps = pp.reference(cat)["current_path"]
    assert cps["promo_first"] == f"system.builtin,system.ai,{promo},{live}"
    assert all(" " not in v for v in cps.values())


def test_duckdb_agrees_and_result_json():
    t = pp.reference()["totals"]
    assert pp.duckdb_totals() == {"live": t["live"], "promo_first": t["promo_first"]}
    saved = json.loads((FEATURE / "result.json").read_text())
    s = pp.summarise()
    assert {k: saved[k] for k in s} == json.loads(json.dumps(s))
    assert s["everyday_cents"] == 2050 and s["promo_saving_cents"] == 305


def test_cloud_compare():
    good = pp.reference("workspace")
    assert rc.compare(json.loads(json.dumps(good)), "workspace") == []
    bad = json.loads(json.dumps(good))
    bad["totals"]["view_captured"] = bad["totals"]["direct_after_view"]  # view re-resolved under the new path
    bad["errors"]["one_part"] = "accepted"
    errs = rc.compare(bad, "workspace")
    assert len(errs) == 2 and errs[0].startswith("totals") and errs[1].startswith("errors")


def test_payload_and_refusal():
    p = rc.submit_payload("/Workspace/x/path_prices.py", "/Volumes/w/s/v/run", "workspace")
    assert p["tasks"][0]["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/run", "workspace"]
    assert p["environments"][0]["spec"]["environment_version"] == "6"
    assert rc.compute_refused(rc.ApiError(400, "FEATURE_DISABLED: x"))
    assert rc.from_logs("a\nDAY47_RESULT {\"totals\": {}}\n") == {"totals": {}}
