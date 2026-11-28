"""Day 53 Databricks: bitemporal AUTO CDC over a late, restating shelf-price feed. The Python row builder, a DuckDB
as-of query over those rows and a DuckDB query straight from the feed must agree after each round; the pipeline SQL
must use the bitemporal clauses; the cloud comparison and refusal handling must behave. No credentials needed."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-53-shelf-price-bitemporal"
spec = importlib.util.spec_from_file_location("bitemporal53", FEATURE / "bitemporal.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_three_references_agree_every_round():
    grids = m.run_local()
    assert [len(g) for g in grids] == [42, 140]


def test_what_we_believed_vs_what_we_know():
    g = m.run_local()[-1]
    assert g["turkey|22|2"] == 1.49 and g["turkey|22|3"] == 1.29      # late change on the 22nd
    assert g["turkey|24|5"] == 0.99 and g["turkey|21|5"] == 1.49
    assert g["cranberry|21|3"] == 2.99 and g["cranberry|21|4"] == 2.79  # restated from the 20th
    assert g["stuffing|23|2"] == 2.99 and g["stuffing|23|5"] == 3.49    # promo cancelled in batch 5
    assert g["pie_crust|25|4"] is None and g["pie_crust|25|5"] == 4.99 and g["pie_crust|24|5"] is None


def test_old_beliefs_are_closed_not_deleted():
    rows = m.bitemporal_rows(1)
    assert ("cranberry", 2.99, 20, None, 1, 4) in rows and ("cranberry", 2.79, 20, None, 4, None) in rows
    assert ("turkey", 1.49, 20, 24, 2, 3) in rows                     # believed in batch 2, replaced in batch 3
    current = [r for r in rows if r[5] is None]
    assert len(current) == 6 and len(rows) == 12
    assert m.bitemporal_rows(0) == [r for r in m.bitemporal_rows(0)]  # deterministic
    assert all(r[4] <= 2 for r in m.bitemporal_rows(0))


def test_pipeline_sql_uses_bitemporal_cdc():
    q = m.pipeline_sql("cat.sch.price_feed")
    for clause in ("CREATE OR REFRESH STREAMING TABLE shelf_prices", "AUTO CDC INTO shelf_prices",
                   "FROM STREAM(cat.sch.price_feed)", "KEYS (sku)", "SEQUENCE BY effective_day",
                   "SYSTEM SEQUENCE BY feed_batch", "STORED AS BITEMPORAL"):
        assert clause in q, clause
    assert "{source}" not in q
    for unsupported in ("IGNORE NULL UPDATES", "APPLY AS TRUNCATE", "TRACK HISTORY ON", "COLUMNS TO UPDATE"):
        assert unsupported not in q                                  # not allowed with BITEMPORAL
    flow = q[q.index("CREATE FLOW"):]
    assert flow.index("\nSEQUENCE BY effective_day") < flow.index("SYSTEM SEQUENCE BY") < flow.index("STORED AS")


def test_compare_is_on_answers_not_rows():
    local = m.run_local()
    # a cloud table that splits a version into two adjacent business-time rows gives the same answers
    rows = [r for r in m.bitemporal_rows(1) if r != ("pie_crust", 4.99, 25, None, 5, None)]
    rows += [("pie_crust", 4.99, 25, 26, 5, None), ("pie_crust", 4.99, 26, None, 5, None)]
    assert m.compare([local[0], m.grid(rows, 1)], local) == []
    wrong = dict(local[1]); wrong["turkey|22|5"] = 1.49
    errs = m.compare([local[0], wrong], local)
    assert len(errs) == 1 and "round 2: 1 of 140" in errs[0] and "turkey|22|5" in errs[0]
    assert m.compare(local[:1], local) == ["1 rounds read, 2 expected"]


def test_refusals_are_classified():
    assert m.compute_refused("FEATURE_DISABLED: jobs are not enabled")
    assert m.compute_refused("The request could not be processed by the warehouse")
    assert m.beta_unavailable("[PARSE_SYNTAX_ERROR] near 'BITEMPORAL'")
    assert m.beta_unavailable("SYSTEM SEQUENCE BY is not supported")
    assert not m.beta_unavailable("TABLE_OR_VIEW_NOT_FOUND")


def test_redaction(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-abc.cloud.databricks.com")
    out = m._redact("at dbc-abc.cloud.databricks.com by a.b@example.com pipeline 12345678")
    assert "dbc-abc" not in out and "@" not in out and "12345678" not in out


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    s = json.loads(json.dumps(m.summarise(m.run_local())))
    assert result["rows"] == s["rows"] and result["headline"] == s["headline"]
    assert result["answers_revised_later"] == 40
