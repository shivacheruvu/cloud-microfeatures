"""Day 19 Google Cloud: the parse_pack Python UDF body reads the label formats on the shelf (multipacks, fl oz vs oz,
L vs l, counts, no size), the DuckDB run of unit_prices.sql with it registered equals the plain-Python model, the
generated BigQuery DDL embeds the file verbatim, and the cloud comparison accepts a match and rejects a change
(no cloud)."""
import importlib.util
import json
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-19-pack-size-udf"
spec = importlib.util.spec_from_file_location("pack_prices_day19", FEATURE / "pack_prices.py")
pp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pp)

LOCAL = pp.run_local()


@pytest.mark.parametrize("label, qty, unit", [
    ("whole milk 1 gal", 3785, "ml"), ("whole milk 64 fl oz", 1893, "ml"), ("2 x 1 l", 2000, "ml"),
    ("12pk 12 fl oz", 4259, "ml"), ("sparkling water 1.5L", 1500, "ml"), ("6 x 500ml", 3000, "ml"),
    ("greek yogurt 32oz", 907, "g"), ("4 x 5.3 oz", 601, "g"), ("2x200g", 400, "g"), ("1.5 lb", 680, "g"),
    ("rice 5 kg", 5000, "g"), ("large eggs 12ct", 12, "ct"), ("eggs 6 pk", 6, "ct"), ("4 × 250 g", 1000, "g"),
    ("family size", None, None), (None, None, None),
])
def test_parse_pack(label, qty, unit):
    assert pp.parse_pack(label) == {"base_qty": qty, "unit": unit}


def test_local_matches_pure_python():
    assert LOCAL == pp.pure_python()


def test_best_values():
    best = {r[0]: r[2] for r in LOCAL if r[8] == 1}
    assert best["milk"] == "whole milk 1 gal" and best["eggs"] == "large eggs 18 count"
    assert best["water"] == "sparkling water 1.5L" and len(best) == 7
    assert [r[2] for r in LOCAL if r[5] is None] == ["tortilla chips family size"]


def test_bigquery_sql():
    ddl = pp.bigquery_sql("udf", "p.ds")
    assert "LANGUAGE python" in ddl and 'entry_point = "parse_pack"' in ddl and 'runtime_version = "python-3.11"' in ddl
    assert (FEATURE / "parse_pack.py").read_text() in ddl and "TEMP" not in ddl
    q = pp.bigquery_sql("query", "p.ds")
    assert "`p.ds`.parse_pack(label)" in q and "{" not in q and not q.lstrip().startswith("--")
    assert pp.bigquery_sql("setup", "p.ds").count("STRUCT(") == len(pp.ITEMS)


def test_compare_and_read(tmp_path):
    out = tmp_path / "query.json"
    out.write_text("WARNING: scopes\n" + json.dumps([{c: (None if v is None else str(v)) for c, v in zip(pp.COLUMNS, r)}
                                                    for r in reversed(LOCAL)]))
    assert pp.compare(pp.read_rows(out), LOCAL) == []
    changed = [list(r) for r in LOCAL]; changed[0][4] += 1
    assert pp.compare([tuple(r) for r in changed], LOCAL)


def test_result_json_is_current():
    assert json.loads((FEATURE / "result.json").read_text())["headline"] == pp.summarise(LOCAL)["headline"]
