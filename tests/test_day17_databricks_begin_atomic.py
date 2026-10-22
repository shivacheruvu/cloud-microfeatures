"""Day 17 Databricks: the local DuckDB transactions match the plain-Python model (bad deliveries leave no trace,
the resent note is refused), the BEGIN ATOMIC block has the checks in the right order, and the cloud comparison
accepts a matching run and rejects a half-booked one (no cloud)."""
import copy
import importlib.util
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-17-atomic-goods-receipts"
spec = importlib.util.spec_from_file_location("receipts_day17", FEATURE / "receipts.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

LOCAL = rc.run_local()


def test_local_matches_pure_python():
    assert rc.compare(LOCAL, rc.pure_python()) == []
    assert LOCAL["outcomes"] == rc.pure_python()["outcomes"]


def test_rejected_deliveries_leave_no_trace():
    booked = {r[0] for r in LOCAL["receipts"]}
    assert booked == {"D1", "D3", "D6"}
    assert [o["reason"] for o in LOCAL["outcomes"]] == [None, "bad line", None, "bad line", "already booked", None]
    assert LOCAL["stock"]["0901"] == 10            # D4's ice cream line was good but rolled back with the bad one
    assert sum(1 for r in LOCAL["receipts"] if r[0] == "D1") == 3   # resent D1 not booked twice


def test_summary_numbers():
    s = rc.summarise(LOCAL)
    assert s["committed"] == ["D1", "D3", "D6"] and s["units_booked"] == 216
    assert s["units_kept_out_by_rollback"] == 88   # D2 bananas + apples (70) and D4 ice cream (18)
    assert sum(t["received"] for t in s["stock"]) == s["units_booked"]


def test_atomic_block():
    d, _, lines = rc.DELIVERIES[1]
    sql = rc.atomic_block(d, lines)
    assert sql.startswith("BEGIN ATOMIC") and sql.rstrip().endswith("END")
    order = [sql.index(k) for k in ("already booked", "INSERT INTO", "bad line", "MERGE INTO")]
    assert order == sorted(order)
    assert "('D2', 3, '4225', 0)" in sql and "{" not in sql
    assert sql.count("SIGNAL SQLSTATE '45000'") == 2
    for name in ("create_stock", "create_receipts"):
        assert "'delta.feature.catalogManaged' = 'supported'" in rc.sql_blocks()[name]


def test_reason_from_signal_message():
    assert rc.reason_from("[USER_RAISED_EXCEPTION] rejected D2: bad line (qty <= 0 or unknown SKU) SQLSTATE: 45000") \
        == "bad line"
    assert rc.reason_from("rejected D1: already booked") == "already booked"
    assert rc.reason_from("TABLE_OR_VIEW_NOT_FOUND") is None


def test_compare_accepts_match_and_rejects_half_booked():
    cloud = copy.deepcopy(LOCAL)
    cloud["receipts"] = [list(r) for r in cloud["receipts"]]      # API rows come back as lists
    assert rc.compare(cloud, LOCAL) == []
    half = copy.deepcopy(LOCAL)                                   # D2's good lines leaked without a transaction
    half["receipts"] += [("D2", 1, "4011", 40), ("D2", 2, "3283", 30)]
    half["stock"]["4011"] += 40
    assert len(rc.compare(half, LOCAL)) == 3
    flipped = copy.deepcopy(LOCAL)
    flipped["outcomes"][1] = {**flipped["outcomes"][1], "status": "committed", "reason": None}
    assert rc.compare(flipped, LOCAL)
