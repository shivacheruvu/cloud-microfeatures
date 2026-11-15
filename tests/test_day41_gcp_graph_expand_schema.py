"""Day 41 Google Cloud: DuckDB equals the plain-Python model, the graph DDL and the expected GRAPH_EXPAND schema come
from one definition, the schema reader accepts the procedure's JSON in a few plausible shapes and flags a missing
column or a wrong measure flag, and the rollup comparison accepts a match and rejects a wrong number (no cloud)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-41-aisle-graph-schema"
spec = importlib.util.spec_from_file_location("aisle_schema_day41", FEATURE / "aisle_schema.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

LOCAL = m.run_local()
RESULT = json.loads((FEATURE / "result.json").read_text())


def fake_schema(drop=None, flip=None, nested=False):
    fields = []
    for col, v in m.expected_schema().items():
        if col == drop:
            continue
        f = {"name": col, "type": "INT64" if v["measure"] else "STRING", "mode": "NULLABLE"}
        if v["measure"] != (col == flip):
            f["is_measure"] = True
        if v["description"]:
            f.update({"options": {"description": v["description"]}} if nested else {"description": v["description"]})
        fields.append(f)
    return {"fields": fields}


def test_local_matches_pure_python():
    assert LOCAL == m.pure_python()


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(m.summarise(LOCAL)))


def test_numbers():
    rows = {r[0]: dict(zip(m.COLS, r)) for r in LOCAL}
    assert rows["Dairy"]["facings"] == 6 + 4 + 3 and rows["Dairy"]["naive_facings"] == 6 * 3 + 4 + 3
    assert sum(r["scans"] for r in rows.values()) == len(m.SCANS)
    assert sum(r["units"] for r in rows.values()) == sum(s[2] for s in m.SCANS) == 227
    s = m.summarise(LOCAL)
    assert (s["facings_true"], s["facings_naive"], s["graph_expand_columns"], s["measure_columns"]) == (31, 64, 12, 3)


def test_generated_sql():
    setup = m.bigquery_sql("setup", "p.d")
    assert "CREATE OR REPLACE PROPERTY GRAPH `p.d`.AisleGraph" in setup
    assert 'MEASURE(SUM(facings)) AS total_facings OPTIONS(description="Shelf facings, counted once per product")' in setup
    assert setup.count("OPTIONS(description=") == 7 and setup.count("NO PROPERTIES") == 2
    assert "OPTIONS" not in m.bigquery_sql("setup-plain", "p.d")
    q = m.bigquery_sql("schema", "p.d", "p.d.AisleGraph")
    assert "CALL BQ.SHOW_GRAPH_EXPAND_SCHEMA('p.d.AisleGraph', graph_schema);" in q and not q.lstrip().startswith("--")
    r = m.bigquery_sql("rollup", "p.d")
    assert 'GRAPH_EXPAND("p.d.AisleGraph")' in r and "AGG(Products_total_facings)" in r
    # every column the rollup reads is in the contract
    cols = set(m.expected_schema())
    assert {"Aisles_aisle_name", "Products_product_id", "ShelfScans_scan_count", "Products_facings",
            "Products_total_facings", "ShelfScans_total_units"} <= cols


def test_check_schema_shapes(tmp_path, capsys):
    for nested in (False, True):
        c = m.check_schema(m.parse_schema(fake_schema(nested=nested)))
        assert c["ok"] and c["descriptions_found"] == 7 and not c["extra"]
    c = m.check_schema(m.parse_schema(fake_schema(drop="Products_facings")))
    assert not c["ok"] and c["missing"] == ["Products_facings"]
    c = m.check_schema(m.parse_schema(fake_schema(flip="ShelfScans_units")))
    assert not c["ok"] and c["measure_wrong"] == ["ShelfScans_units"]
    # the procedure output arrives as a JSON string inside a bq row, after a warning line
    f = tmp_path / "schema.json"
    f.write_text("WARNING: --scopes ignored\n" + json.dumps([{"graph_schema": json.dumps(fake_schema())}]))
    assert m.main(["--check-schema", str(f)]) == 0
    assert "all 12 contract columns present" in capsys.readouterr().out
    plain = fake_schema()
    for fld in plain["fields"]:
        fld.pop("description", None)
    f.write_text(json.dumps([{"graph_schema": json.dumps(plain)}]))
    assert m.main(["--check-schema", str(f)]) == 0
    assert "::warning" in capsys.readouterr().out
    f.write_text(json.dumps([{"graph_schema": json.dumps(fake_schema(drop="Aisles_aisle_name"))}]))
    assert m.main(["--check-schema", str(f)]) == 1


def test_cloud_compare(tmp_path, capsys):
    rows = [{c: (str(v) if i else v) for i, (c, v) in enumerate(zip(m.COLS, r))} for r in LOCAL]
    f = tmp_path / "rollup.json"
    f.write_text(json.dumps(rows))
    assert m.main(["--compare", str(f)]) == 0
    assert "matched the local model" in capsys.readouterr().out
    rows[0]["facings"] = "64"
    f.write_text(json.dumps(rows))
    assert m.main(["--compare", str(f)]) == 1
