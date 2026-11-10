# Day 36 · Google Cloud · Archive raw scan exports as Zstandard BYTES: ZSTD_COMPRESS in BigQuery

**New platform feature:** **GoogleSQL compression functions** `ZSTD_COMPRESS`, `ZSTD_DECOMPRESS_TO_BYTES` and
`ZSTD_DECOMPRESS_TO_STRING` (Preview): compress a `STRING` or `BYTES` value with Zstandard (level -5 to 22, default 3)
and decompress it back, with an optional `size_limit` (default 1 GiB) that makes decompression fail rather than
return something larger. Announced in the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) on **2026-11-09**
([reference: compression functions](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/compression-functions)).

## What it does
Five raw store scan exports for 2026-11-08 ([`data/`](data/), synthetic, CSV with UTF-8 product names such as
*Crème fraîche* and *Ñoquis*; one is a header-only file) are inlined in one query
([`compress_exports.sql`](compress_exports.sql)) that:

- compresses each export at levels -5, 1, 3, 19 and with no level (must be identical to level 3), from `STRING` and
  from `BYTES` (must be identical);
- reads every frame back with `ZSTD_DECOMPRESS_TO_STRING`, and level 3 / level 19 with `ZSTD_DECOMPRESS_TO_BYTES`
  (once with an explicit 1 MiB `size_limit`).

`scan_archive.py --compare` then decompresses **every frame BigQuery returned locally with plain libzstd** (python
`zstandard`) and requires the exact export text and the uncompressed size in the frame header: a BigQuery-archived
blob is readable by any zstd tool, not just by BigQuery. Compressed sizes depend on the zstd build, so they are
reported (with how many frames are byte-identical to local libzstd), not required to match.

A second query ([`size_limit_guard.sql`](size_limit_guard.sql)) decompresses the 9.9 KB NYC export with
`size_limit => 4096`; per the docs it must fail with "ZSTD output is too large" (reported as a notice, a warning if
BigQuery behaves differently).

## Why it matters
Raw exports kept for audit or replay compress 5-6x as text; before these functions that meant compressing outside
BigQuery and losing the ability to unpack in SQL. Now a `BYTES` archive column can be written and read in SQL, and the
frames stay standard zstd. The tiny export shows the flip side: a 47-byte file *grows* to 56 bytes (frame overhead),
so small payloads are better left alone.

## How to run
```bash
python features/gcp/day-36-scan-archive-zstd/scan_archive.py      # local libzstd run, writes result.json
pytest -q tests/test_day36_gcp_zstd_compress.py
GCP_PROJECT_ID=... bash features/gcp/day-36-scan-archive-zstd/cloud.sh   # in GitHub Actions (keyless)
```
`make_data.py` regenerates the sample exports (fixed seed).

## Cost
Two queries over inlined literals: 0 bytes scanned, so $0 under on-demand pricing (`--maximum_bytes_billed`
20 MiB cap). Nothing is created.

## Result (local, libzstd 1.5.7)
| Level | Compressed (all 5 exports) | Ratio |
|---|---|---|
| -5 | 9,874 B | 3.13x |
| 1 | 5,558 B | 5.56x |
| 3 (default) | 5,486 B | 5.64x |
| 19 | 4,948 B | 6.25x |

30,927 raw bytes (269 extra bytes from multi-byte UTF-8 characters, so `BYTE_LENGTH` ≠ `CHAR_LENGTH`). Level 3 per
export: 5.2x-6.0x; `tiny-05` grows 47 → 56 B.

**Cloud (2026-11-09, after #131 merged): ran and matched.** All 20 BigQuery frames decompress with libzstd to the
exact exports with the size in the header, no checksum flag; the default level is 3; and **all 20 frames are
byte-identical to libzstd 1.5.7**, so BigQuery's sizes equal the table above. The size_limit guard refused as
documented: "ZSTD output is too large: (9883 bytes) > limit (4096 bytes)". Numbers in
[`result.json`](result.json).
