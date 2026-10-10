"""Day 5 Databricks: the local integer-RANGE reference agrees with plain Python, the fiscal-week index is dense across
the year boundary, and the metric view YAML declares unitless window measures on the numeric index."""
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-05-fiscal-week-sales"
spec = importlib.util.spec_from_file_location("fiscal_weeks", FEATURE / "fiscal_weeks.py")
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)


def python_reference(rows, by_store=True):
    weekly = defaultdict(int)
    for r in rows:
        weekly[(r["store"] if by_store else "all", int(r["week_index"]))] += int(r["units"])
    out = []
    for store, w in sorted(weekly):
        prior = [weekly[(store, k)] for k in range(w - 4, w) if (store, k) in weekly]
        out.append({"store": store, "week_index": w, "units_sold": weekly[(store, w)],
                    "units_prev_week": weekly.get((store, w - 1)),
                    "units_prior_4wk": sum(prior) if prior else None,
                    "units_to_date": sum(v for (s, k), v in weekly.items() if s == store and k <= w)})
    return out


def test_local_matches_python_reference():
    rows = fw.load_rows()
    local = fw.run_local(rows)
    assert local["by_store"] == python_reference(rows)
    assert local["all_stores"] == python_reference(rows, by_store=False)


def test_prev_week_crosses_the_fiscal_year_boundary():
    rows = fw.load_rows()
    labels = fw.week_labels(rows)
    allr = {labels[r["week_index"]]: r for r in fw.run_local(rows)["all_stores"]}
    assert allr["FY2027 W01"]["units_prev_week"] == allr["FY2026 W52"]["units_sold"]
    assert allr["FY2026 W49"]["units_prev_week"] is None and allr["FY2026 W49"]["units_prior_4wk"] is None


def test_gappy_index_is_rejected():
    rows = [r for r in fw.load_rows() if r["week_index"] != "5"]
    with pytest.raises(ValueError, match="not dense"):
        fw.run_local(rows)


def test_spikes_are_the_promo_weeks():
    rows = fw.load_rows()
    spikes = fw.spikes(fw.run_local(rows)["by_store"], fw.week_labels(rows))
    assert [(s["store"], s["week"]) for s in spikes] == [("north", "FY2027 W03"), ("south", "FY2027 W06")]


def test_metric_view_declares_numeric_index_windows():
    sql = fw.metric_view_sql()
    assert "WITH METRICS" in sql and "LANGUAGE YAML" in sql and "version: 1.1" in sql
    assert sql.count("order: week_index") == 3 and sql.count("semiadditive: last") == 3
    assert "offset: -1" in sql and "range: trailing 4\n" in sql and "range: cumulative" in sql
    assert " day" not in sql.split("$$")[1]  # unitless: no calendar units in the YAML
    q = fw.queries()
    assert list(q) == ["by_store", "all_stores"]
    for name in ("units_sold", "units_prev_week", "units_prior_4wk", "units_to_date"):
        assert all(f"MEASURE({name})" in s for s in q.values())
    assert "CREATE OR REPLACE TABLE microfeatures_day05.weekly_units" in fw.table_sql(fw.load_rows()[:1])


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    rows = fw.load_rows()
    summary = fw.summarise(fw.run_local(rows), fw.week_labels(rows))
    assert {k: result[k] for k in summary} == summary
