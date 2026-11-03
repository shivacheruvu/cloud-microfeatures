"""Day 29 Google Cloud: the price book decodes identically from a BigQuery-shaped Arrow response (plain and ZSTD) and
from default JSON rows, result.json is current, and the cloud runner sends the three jobs.query requests, compares,
and reports failures without the project ID (against a fake REST endpoint built from the local payloads)."""
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")
FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-29-price-book-arrow"
spec = importlib.util.spec_from_file_location("day29_price_book", FEATURE / "price_book.py")
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)
RESULT = json.loads((FEATURE / "result.json").read_text())


def _local():
    rows = pb.price_book()
    return pb.local_json_response(rows), pb.local_arrow_response(rows), pb.local_arrow_response(rows, "zstd")


def test_result_json_is_current():
    got = json.loads(json.dumps(pb.evaluate(*_local())))
    assert {k: v for k, v in RESULT.items() if k != "engine"} == got


def test_all_three_decodings_agree_and_are_typed():
    j, a, z = _local()
    ref = pb.canonical(pb.price_book())
    assert pb.canonical(pb.decode_json(j)[0]) == ref
    table, _ = pb.decode_arrow(a)
    assert pb.canonical(pb.arrow_rows(table)) == ref == pb.canonical(pb.arrow_rows(pb.decode_arrow(z)[0]))
    assert pb.arrow_types(table) == pb.ARROW_TYPES
    first = pb.arrow_rows(table)[0]
    assert isinstance(first["price"], Decimal) and isinstance(first["pack_g"], int)
    assert sum(r["updated_at"].microsecond != 0 for r in pb.arrow_rows(table)) >= 119   # microseconds survive


def test_price_book_numbers():
    pb_ = RESULT["price_book"]
    assert RESULT["rows"] == 120 and pb_["promo_rows"] == 10
    assert RESULT["payload_bytes"]["arrow_zstd"] < RESULT["payload_bytes"]["arrow"] < RESULT["payload_bytes"]["json_rows"]
    oat_west = next(r for r in pb.price_book() if r["store"] == "west" and r["sku"] == "0001")
    assert oat_west["on_promo"] and oat_west["price"] == Decimal("4.04")


def test_sql_inlines_typed_literals():
    s = pb.sql()
    assert s.count("STRUCT(") == 120 and "NUMERIC '4.49'" in s and "DATE '2026-11-02'" in s
    assert "ROUND(price * 1000 / pack_g, 2)" in s and not s.lstrip().startswith("SELECT")


class FakeBQ:
    def __init__(self, broken=False):
        self.j, self.a, self.z = _local()
        self.bodies, self.broken = [], broken

    def __call__(self, body):
        self.bodies.append(body)
        if body.get("queryResultsFormat") == "ARROW":
            if self.broken:   # one price wrong in the Arrow batch
                rows = pb.price_book()
                rows[0]["price"] += Decimal("0.01")
                return pb.local_arrow_response(rows)
            return self.z if "arrowSerializationOptions" in body else self.a
        return self.j


def test_cloud_against_fake_endpoint(monkeypatch, capsys):
    monkeypatch.setenv("GCP_PROJECT_ID", "my-proj-123")
    fake = FakeBQ()
    assert pb.cloud(post=fake) == 0
    out = capsys.readouterr().out
    assert "::notice" in out and "all 120" in out and "my-proj-123" not in out
    assert [b.get("queryResultsFormat") for b in fake.bodies] == [None, "ARROW", "ARROW"]
    assert fake.bodies[0]["formatOptions"] == {"useInt64Timestamp": True}
    assert fake.bodies[2]["arrowSerializationOptions"] == {"bufferCompression": "ZSTD"}
    assert all(b["maximumBytesBilled"] == "20971520" and b["useLegacySql"] is False for b in fake.bodies)


def test_cloud_flags_a_wrong_value(monkeypatch, capsys):
    monkeypatch.setenv("GCP_PROJECT_ID", "my-proj-123")
    assert pb.cloud(post=FakeBQ(broken=True)) == 1
    assert "arrow_matches_reference is false" in capsys.readouterr().out


def test_cloud_reports_incomplete_job_and_redacts(monkeypatch, capsys):
    monkeypatch.setenv("GCP_PROJECT_ID", "my-proj-123")
    assert pb.cloud(post=lambda body: {"jobComplete": False}) == 1
    assert "getQueryResults can't return Arrow" in capsys.readouterr().out
    assert pb._redact("Access denied in my-proj-123 for 1234567890 a@b.iam.gserviceaccount.com") == \
        "Access denied in <project> for <number> <account>"
