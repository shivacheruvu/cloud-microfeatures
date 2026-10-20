"""Day 15 Google Cloud: the local Python gives the expected late-row counts per feed and store, a DuckDB table macro
(DuckDB's counterpart of an ANY TABLE function) applied to the three differently shaped tables agrees, the BigQuery
script defines the function once with an ANY TABLE parameter and calls it on all three feeds with the same SLAs, the
data files are reproducible, and the bq output reader and comparison work (no cloud)."""
import importlib.util
import json
from pathlib import Path

import duckdb

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-15-feed-freshness"
spec = importlib.util.spec_from_file_location("freshness_day15", FEATURE / "freshness.py")
fr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fr)

DATA = fr.load()
LOCAL = fr.run_local(DATA)


def as_bq(local):  # bq --format=json prints INT64 values as strings
    return [{"feed": f, "store": s, **{k: str(v) for k, v in r.items()}} for (f, s), r in sorted(local.items())]


def test_data_is_reproducible():
    assert fr.make_data() == DATA


def test_feeds_have_different_shapes():
    cols = {f: set(rows[0]) for f, rows in DATA.items()}
    assert all({"store", "event_ts", "loaded_ts"} <= c for c in cols.values())
    assert len({frozenset(c) for c in cols.values()}) == 3


def test_expected_answers():
    assert len(LOCAL) == 9 and sum(r["n_rows"] for r in LOCAL.values()) == 129
    assert LOCAL[("deliveries", "S3")] == {"n_rows": 5, "late_rows": 5, "max_delay_min": 521}
    assert LOCAL[("scans", "S2")]["late_rows"] == 6 and LOCAL[("scans", "S1")]["late_rows"] == 0
    s = fr.summarise(LOCAL, DATA)
    assert s["late_rows"] == 14 and len(s["breaches"]) == 4 and s["worst"]["feed"] == "deliveries"


def test_minutes_truncate_like_timestamp_diff():
    assert fr._minutes({"event_ts": "2026-10-19 10:00:59", "loaded_ts": "2026-10-19 10:15:58"}) == 14
    assert fr._minutes({"event_ts": "2026-10-19 10:00:00", "loaded_ts": "2026-10-19 10:15:00"}) == 15


def test_duckdb_table_macro_agrees():
    con = duckdb.connect()
    for name in DATA:
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_csv('{FEATURE / 'data' / (name + '.csv')}', "
                    "types={'event_ts': 'TIMESTAMP', 'loaded_ts': 'TIMESTAMP'})")
    con.execute("""
        CREATE MACRO late_rows(t, feed_name, sla) AS TABLE
        SELECT feed_name AS feed, store, COUNT(*) AS n_rows,
               COUNT(*) FILTER (WHERE (epoch(loaded_ts) - epoch(event_ts)) // 60 > sla) AS late_rows,
               MAX(CAST((epoch(loaded_ts) - epoch(event_ts)) // 60 AS BIGINT)) AS max_delay_min
        FROM query_table(t) GROUP BY store""")
    sql = " UNION ALL ".join(f"SELECT * FROM late_rows('{f}', '{f}', {m})" for f, m in fr.SLA.items())
    rows = con.execute(sql).fetchall()
    cloud = [dict(zip(("feed", "store", "n_rows", "late_rows", "max_delay_min"), map(str, r))) for r in rows]
    assert fr.compare(cloud, LOCAL) == []


def test_bigquery_script_shape():
    sql = fr.bigquery_sql(DATA, "proj.mf_day15_1")
    assert "CREATE OR REPLACE TABLE FUNCTION `proj.mf_day15_1.late_rows`(input_table ANY TABLE" in sql
    assert "TEMP" not in sql                        # BigQuery has no temporary table functions (yet)
    for feed, minutes in fr.SLA.items():
        assert f"CREATE OR REPLACE TABLE `proj.mf_day15_1.{feed}` AS" in sql
        assert f"`proj.mf_day15_1.late_rows`(TABLE `proj.mf_day15_1.{feed}`, '{feed}', {minutes})" in sql
    assert "{ds}" not in sql
    assert all("{" + f + "}" not in sql for f in fr.SLA)  # every placeholder filled
    assert sql.count("STRUCT(") == 129


def test_read_bq_takes_the_result_rows(tmp_path):
    out = tmp_path / "out.json"
    out.write_text("WARNING: --scopes ...\n[]\n" + json.dumps(as_bq(LOCAL)) + "\n")
    assert fr.read_bq(str(out)) == as_bq(LOCAL)
    # the shape bq printed for the script in the 2026-10-19 cloud run: DDL status strings, then the rows nested
    script_out = ['Created p.mf_day15_1.scans\n', 'Created p.mf_day15_1.deliveries\n',
                  'Created p.mf_day15_1.price_tags\n', 'Created p.mf_day15_1.late_rows', as_bq(LOCAL)]
    out.write_text("WARNING: `--scopes` flag may not work as expected\n" + json.dumps(script_out).replace(",", "\n,"))
    assert fr.read_bq(str(out)) == as_bq(LOCAL)


def test_compare_accepts_match_and_rejects_differences():
    assert fr.compare(as_bq(LOCAL), LOCAL) == []
    assert fr.compare(as_bq(LOCAL)[1:], LOCAL)                       # missing row
    assert fr.compare(as_bq(LOCAL) + as_bq(LOCAL)[:1], LOCAL)        # duplicate row
    off = as_bq(LOCAL)
    off[0]["late_rows"] = str(int(off[0]["late_rows"]) + 1)          # one late row too many
    assert fr.compare(off, LOCAL)
