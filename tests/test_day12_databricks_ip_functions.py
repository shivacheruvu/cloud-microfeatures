"""Day 12 Databricks: the local device audit gives the planted outcomes, the data files are reproducible, the SQL is
built from them, and the cloud comparison accepts Statement-Execution-shaped rows that match and rejects any
difference (no cloud)."""
import importlib.util
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-12-lane-device-audit"
spec = importlib.util.spec_from_file_location("audit_day12", FEATURE / "audit.py")
au = importlib.util.module_from_spec(spec)
spec.loader.exec_module(au)

SUBNETS, DEVICES = au.load()
LOCAL = au.run_local(SUBNETS, DEVICES)


def test_data_is_reproducible():
    assert au.make_data() == (SUBNETS, DEVICES)


def test_planted_outcomes():
    by_id = {r["device_id"]: r for r in LOCAL}
    assert by_id["S1-SCL-2"]["status"] == "wrong_store" and by_id["S1-SCL-2"]["seen_store"] == "S4"
    assert by_id["S2-HH-1"]["status"] == "wrong_store" and by_id["S2-HH-1"]["seen_store"] == "S3"
    assert by_id["S3-REG-4"]["status"] == "unknown_network" and by_id["S4-HH-2"]["status"] == "unknown_network"
    assert by_id["S2-SCO-2"]["status"] == "bad_ip" and by_id["S2-SCO-2"]["ip"] is None
    assert by_id["S4-REG-3"]["status"] == "bad_ip"
    assert by_id["S1-HH-2"] == {"device_id": "S1-HH-2", "ip": "2001:db8:12:1::a2", "ip_version": 6,
                                "seen_store": "S1", "status": "ok"}
    assert all(r["status"] == "ok" for r in LOCAL if r["device_id"] not in au.PLANTED)


def test_summary_numbers():
    s = au.summarise(LOCAL, DEVICES)
    assert (s["devices"], s["ok"], s["wrong_store"], s["unknown_network"], s["bad_ip"]) == (40, 34, 2, 2, 2)
    assert (s["ipv4"], s["ipv6"], s["canonicalised"]) == (30, 8, 3)


def cloud_rows(rows):  # the Statement Execution API returns every value as a string (or null)
    return [[r["device_id"], r["ip"], None if r["ip_version"] is None else str(r["ip_version"]),
             r["seen_store"], r["status"]] for r in rows]


def test_compare_accepts_match_and_rejects_differences():
    assert au.compare(au.rows_to_result(cloud_rows(LOCAL)), LOCAL) == []
    assert au.compare(au.rows_to_result(cloud_rows(LOCAL))[1:], LOCAL)          # missing device
    off = cloud_rows(LOCAL)
    i = next(n for n, r in enumerate(off) if r[0] == "S1-HH-2")
    off[i][1] = "2001:0db8:0012:0001:0000:0000:0000:00a2"                         # not canonical
    assert au.compare(au.rows_to_result(off), LOCAL)
    off = cloud_rows(LOCAL)
    off[0][4] = "wrong_store"
    assert au.compare(au.rows_to_result(off), LOCAL)


def test_sql_uses_new_ip_functions():
    sql = au.sql_statements(SUBNETS, DEVICES)["databricks"]
    for fn in ("try_ip_host(raw_ip)", "ip_cidr_contains(s.cidr, d.ip)", "ip_version(ip)"):
        assert fn in sql
    assert "{devices}" not in sql and "'lane-07'" in sql and "'2001:db8:12:4::/64'" in sql
    assert ";" not in sql
