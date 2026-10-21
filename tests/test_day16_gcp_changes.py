"""Day 16 Google Cloud: the local replay emits the change rows CHANGES documents (INSERT, DELETE, and an UPDATE as an
old DELETE row flagged is_for_update plus a new UPDATE row), the SQL creates the table with enable_change_history and
reads it with CHANGES, and the cloud check reads bq JSON (strings, a warning line first) and rejects differences."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-16-shelf-price-changes"
spec = importlib.util.spec_from_file_location("changes_day16", FEATURE / "price_changes.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)

LOCAL = pc.run_local()


def test_replay_follows_changes_semantics():
    by = {}
    for r in LOCAL:
        by.setdefault((r["commit_no"], r["sku"]), []).append((r["change_type"], r["is_for_update"], r["price_cents"]))
    assert len({r["commit_no"] for r in LOCAL}) == 5
    assert all(r["change_type"] == "INSERT" for r in LOCAL if r["commit_no"] == 1) and len(by) == 10 + 3 + 2 + 1 + 1
    assert sorted(by[(2, "P03")]) == [("DELETE", True, 429), ("UPDATE", False, 449)]
    assert sorted(by[(3, "P03")]) == [("DELETE", True, 449), ("UPDATE", False, 399)]
    assert by[(4, "P09")] == [("DELETE", False, 359)]
    assert by[(5, "P11")] == [("INSERT", False, 599)]


def test_summary():
    s = pc.summarise(LOCAL)
    assert s["change_rows"] == 22 and s["by_type"] == {"DELETE": 6, "INSERT": 11, "UPDATE": 5}
    assert s["price_updates"] == 5 and s["repriced_twice"] == ["P03"]
    assert s["removed"] == ["P09"] and s["added"] == ["P11"]
    assert s["net_change_cents"] == {"P01": 20, "P02": 20, "P03": -30, "P07": -50}


def test_sql():
    setup = pc.bigquery_sql("setup", "p.ds")
    assert "OPTIONS (enable_change_history = TRUE)" in setup and setup.count(";") == 6
    assert "TRANSACTION" not in setup.upper()  # a script, not a multi-statement transaction
    assert not setup.lstrip().startswith("--")
    q = pc.bigquery_sql("changes", "p.ds")
    assert "FROM CHANGES(TABLE `p.ds.shelf_prices`, NULL, NULL)" in q and ";" not in q


def bq_json(rows):
    return [{"commit_no": str(r["commit_no"]), "sku": r["sku"], "price_cents": str(r["price_cents"]),
             "change_type": r["change_type"], "is_for_update": "true" if r["is_for_update"] else "false"}
            for r in rows]


def test_compare_reads_bq_output(tmp_path):
    out = tmp_path / "out.json"
    out.write_text("WARNING: scopes\n" + json.dumps(bq_json(LOCAL)))
    assert pc.compare(pc.read_bq(str(out)), LOCAL) == []
    assert pc.summarise(pc.read_bq(str(out)))["price_updates"] == 5


def test_compare_rejects_differences():
    rows = bq_json(LOCAL)
    assert pc.compare(rows[1:], LOCAL)
    bad = [dict(r) for r in rows]
    bad[-1]["price_cents"] = "1"
    assert pc.compare(bad, LOCAL)
    flipped = [dict(r, is_for_update="false") if r["change_type"] == "DELETE" else r for r in rows]
    assert pc.compare(flipped, LOCAL)
