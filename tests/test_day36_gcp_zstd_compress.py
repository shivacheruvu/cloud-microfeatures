"""Day 36 Google Cloud: the scan exports compress and round-trip with libzstd at every level, the frames carry their
content size, the SQL inlines every export (UTF-8 and newlines escaped), and the compare step accepts BigQuery-shaped
output and catches a bad frame, a wrong round-trip flag or a default level that isn't 3."""
import base64
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-36-scan-archive-zstd"
spec = importlib.util.spec_from_file_location("scan_archive36", FEATURE / "scan_archive.py")
sa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sa)


def _bq_rows():
    # bq's JSON gives INT64 as strings and BOOL as "true"
    return [{**r, "raw_bytes": str(r["raw_bytes"]), "raw_chars": str(r["raw_chars"]),
             **{k: "true" for k in ("string_and_bytes_input_same", "string_round_trip", "bytes_round_trip",
                                    "round_trip_under_1mib_limit")}} for r in sa.local_rows(sa.load_exports())]


def test_round_trip_and_frame_header_at_every_level():
    for e in sa.load_exports():
        for lvl in sa.LEVELS.values():
            blob = sa.compress_local(e["payload"], lvl)
            assert sa.decompress_local(blob) == e["payload"]
            assert sa.frame_info(blob)["content_size"] == len(e["payload"].encode())


def test_summary_numbers():
    s = sa.summarise(sa.local_rows(sa.load_exports()))
    assert s["exports"] == 5 and s["raw_bytes_total"] == 30927 and s["multibyte_chars"] > 0
    t = s["compressed_bytes_total"]
    assert t["level 19"] < t["level 3"] < t["level -5"] < s["raw_bytes_total"]
    assert s["ratio_total"]["level 3"] > 5
    assert s["grew_when_compressed"] == ["tiny-05"]


def test_sql_inlines_every_export_escaped():
    exports = sa.load_exports()
    q = sa.sql(exports)
    assert "{exports}" not in q and q.count("AS payload)") == 5
    assert "ZSTD_COMPRESS(payload, level => 19)" in q and "ZSTD_COMPRESS(payload) AS z_default" in q
    assert "\\n2026-11-08T" in q and "Crème fraîche" in q
    assert "\n2026-11-08T" not in q  # no raw newline inside a string literal
    assert sa._q("it's\na\\b") == "'it\\'s\\na\\\\b'"
    g = sa.guard_sql(exports)
    assert "size_limit => 4096" in g and "{payload}" not in g


def test_compare_accepts_matching_rows(tmp_path, capsys):
    out = tmp_path / "out.json"
    out.write_text("Warning: --scopes ...\n" + json.dumps(_bq_rows()))
    assert sa.main("--compare", str(out)) == 0
    text = capsys.readouterr().out
    assert "::notice" in text and "::error" not in text and "20/20 frames byte-identical" in text


def test_compare_catches_differences(tmp_path, capsys):
    exports = sa.load_exports()
    rows = _bq_rows()
    other = base64.b64encode(sa.compress_local("something else", 3)).decode()
    bad = [dict(rows[0], z_19=other), dict(rows[1], bytes_round_trip="false"),
           dict(rows[2], z_default=rows[2]["z_1"]), dict(rows[3], z_fast=base64.b64encode(b"junk").decode())]
    for i, r in enumerate(bad):
        rr = list(rows)
        rr[i] = r
        errors, _ = sa.check_cloud(rr, exports)
        assert errors, i
    errors, _ = sa.check_cloud(rows[:-1], exports)
    assert errors


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    s = sa.summarise(sa.local_rows(sa.load_exports()))
    assert {k: result[k] for k in s} == s
