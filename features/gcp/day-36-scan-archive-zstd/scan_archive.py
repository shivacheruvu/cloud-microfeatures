"""Day 36 (Google Cloud): archive raw grocery scan exports as Zstandard-compressed BYTES in BigQuery with
ZSTD_COMPRESS, and read them back with ZSTD_DECOMPRESS_TO_STRING / ZSTD_DECOMPRESS_TO_BYTES (Preview, 2026-11-09).

  python scan_archive.py                   # local: libzstd (python `zstandard`) at the same levels; writes result.json
  python scan_archive.py --sql             # print the main BigQuery query (exports inlined, no table needed)
  python scan_archive.py --guard-sql       # print the size_limit guard query
  python scan_archive.py --compare OUT     # check bq's JSON output against the local run (cloud.sh)

Five store scan exports (CSV text with UTF-8 product names, synthetic) are compressed at levels -5, 1, 3 (and the
default, which must be 3) and 19. The check that matters is cross-implementation: every frame BigQuery produced must
decompress locally, with plain libzstd, to the exact export, and carry the uncompressed size in its frame header
(the decompress functions require it). Compressed sizes depend on the zstd build, so they're reported, not required
to match byte for byte.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import zstandard

HERE = Path(__file__).resolve().parent
LEVELS = {"z_fast": -5, "z_1": 1, "z_3": 3, "z_19": 19}
GUARD_BATCH, GUARD_LIMIT = "nyc-04", 4096


def load_exports() -> list[dict]:
    return [{"batch": p.stem, "payload": p.read_text(encoding="utf-8")} for p in sorted((HERE / "data").glob("*.csv"))]


def compress_local(payload: str, level: int) -> bytes:
    return zstandard.ZstdCompressor(level=level, write_content_size=True).compress(payload.encode("utf-8"))


def frame_info(blob: bytes) -> dict:
    p = zstandard.get_frame_parameters(blob)
    return {"content_size": p.content_size, "checksum": bool(p.has_checksum)}


def decompress_local(blob: bytes) -> str:
    return zstandard.ZstdDecompressor().decompress(blob).decode("utf-8")


def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def sql(exports: list[dict]) -> str:
    rows = ",\n  ".join(f"STRUCT({_q(e['batch'])} AS batch, {_q(e['payload'])} AS payload)" for e in exports)
    return (HERE / "compress_exports.sql").read_text().replace("{exports}", f"[\n  {rows}\n]")


def guard_sql(exports: list[dict]) -> str:
    payload = next(e["payload"] for e in exports if e["batch"] == GUARD_BATCH)
    return (HERE / "size_limit_guard.sql").read_text().replace("{payload}", _q(payload))


def local_rows(exports: list[dict]) -> list[dict]:
    rows = []
    for e in exports:
        z = {col: compress_local(e["payload"], lvl) for col, lvl in LEVELS.items()}
        rows.append({"batch": e["batch"], "raw_bytes": len(e["payload"].encode("utf-8")),
                     "raw_chars": len(e["payload"]), **{c: base64.b64encode(b).decode() for c, b in z.items()},
                     "z_default": base64.b64encode(z["z_3"]).decode(), "string_and_bytes_input_same": True,
                     "string_round_trip": True, "bytes_round_trip": True, "round_trip_under_1mib_limit": True})
    return rows


def summarise(rows: list[dict]) -> dict:
    sizes = {r["batch"]: {c: len(base64.b64decode(r[c])) for c in LEVELS} for r in rows}
    raw = {r["batch"]: int(r["raw_bytes"]) for r in rows}
    total_raw = sum(raw.values())
    totals = {c: sum(s[c] for s in sizes.values()) for c in LEVELS}
    return {
        "exports": len(rows),
        "raw_bytes_total": total_raw,
        "multibyte_chars": sum(int(r["raw_bytes"]) - int(r["raw_chars"]) for r in rows),
        "compressed_bytes_total": {f"level {LEVELS[c]}": totals[c] for c in LEVELS},
        "ratio_total": {f"level {LEVELS[c]}": round(total_raw / totals[c], 2) for c in LEVELS},
        "level_3_by_export": {b: {"raw": raw[b], "zstd": sizes[b]["z_3"], "ratio": round(raw[b] / sizes[b]["z_3"], 2)}
                              for b in sorted(raw)},
        "grew_when_compressed": sorted(b for b in raw if sizes[b]["z_3"] >= raw[b]),
    }


def check_cloud(rows: list[dict], exports: list[dict]) -> tuple[list[str], dict]:
    """Errors (strict) and facts (reported) about BigQuery's output."""
    want = {e["batch"]: e["payload"] for e in exports}
    errors: list[str] = []
    facts = {"frames": 0, "byte_identical_to_local": 0, "checksum_flag": set()}
    if sorted(r["batch"] for r in rows) != sorted(want):
        errors.append(f"BigQuery returned batches {sorted(r['batch'] for r in rows)}, local {sorted(want)}")
    for r in rows:
        b, payload = r["batch"], want.get(r["batch"])
        if payload is None:
            continue
        if int(r["raw_bytes"]) != len(payload.encode()) or int(r["raw_chars"]) != len(payload):
            errors.append(f"{b}: BYTE_LENGTH/CHAR_LENGTH {r['raw_bytes']}/{r['raw_chars']} differ from local")
        for flag in ("string_and_bytes_input_same", "string_round_trip", "bytes_round_trip",
                     "round_trip_under_1mib_limit"):
            if str(r[flag]).lower() != "true":
                errors.append(f"{b}: {flag} is {r[flag]} in BigQuery")
        if r["z_default"] != r["z_3"]:
            errors.append(f"{b}: ZSTD_COMPRESS without a level differs from level => 3")
        for c, lvl in LEVELS.items():
            blob = base64.b64decode(r[c])
            facts["frames"] += 1
            try:
                if decompress_local(blob) != payload:
                    errors.append(f"{b} level {lvl}: BigQuery's frame decompresses locally to different text")
            except zstandard.ZstdError as e:
                errors.append(f"{b} level {lvl}: libzstd can't read BigQuery's frame: {e}")
                continue
            info = frame_info(blob)
            facts["checksum_flag"].add(info["checksum"])
            if info["content_size"] != len(payload.encode()):
                errors.append(f"{b} level {lvl}: frame header content size {info['content_size']}")
            facts["byte_identical_to_local"] += blob == compress_local(payload, lvl)
    facts["checksum_flag"] = sorted(facts["checksum_flag"])
    return errors, facts


def read_bq_json(path: str) -> list[dict]:
    raw = Path(path).read_text()
    start = raw.find("[")  # bq may print a warning line before the JSON
    return json.loads(raw[start:]) if start >= 0 else []


def main(*argv: str) -> int:
    exports = load_exports()
    if argv[:1] == ("--sql",):
        print(sql(exports))
    elif argv[:1] == ("--guard-sql",):
        print(guard_sql(exports))
    elif argv[:1] == ("--compare",) and len(argv) > 1:
        try:
            rows = read_bq_json(argv[1])
            errors, facts = check_cloud(rows, exports)
            cloud = summarise(rows) if rows else {}
        except (ValueError, KeyError, TypeError) as e:
            print(f"::error title=Day 36 unreadable BigQuery output::{e!r}"[:500])
            return 1
        for e in errors[:10]:
            print(f"::error title=Day 36 ZSTD result differs from local::{e[:400]}")
        if errors:
            return 1
        local = summarise(local_rows(exports))
        print("::notice title=Google Cloud day 36::ZSTD_COMPRESS / ZSTD_DECOMPRESS_TO_* ran in BigQuery and matched "
              f"local: all {facts['frames']} frames decompress with libzstd to the exact exports, sizes in the "
              f"headers, default level = 3, {facts['byte_identical_to_local']}/{facts['frames']} frames byte-identical "
              f"to libzstd {'.'.join(map(str, zstandard.ZSTD_VERSION))}, checksum flag {facts['checksum_flag']}. "
              f"BigQuery sizes {json.dumps(cloud['compressed_bytes_total'])} (local "
              f"{json.dumps(local['compressed_bytes_total'])}) of {cloud['raw_bytes_total']} raw bytes; ratios "
              f"{json.dumps(cloud['ratio_total'])}; grew: {cloud['grew_when_compressed']}"[:900])
    else:
        summary = summarise(local_rows(exports))
        (HERE / "result.json").write_text(json.dumps({"engine": "libzstd via python zstandard (local) / BigQuery "
                                                      "ZSTD_COMPRESS (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
