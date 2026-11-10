-- Day 36 (Google Cloud): the size_limit guard. The largest export (nyc-04, 9,883 bytes) decompressed with a 4 KiB
-- limit must fail with "ZSTD output is too large", so a corrupt or hostile blob can't blow up a query's memory.
SELECT BYTE_LENGTH(ZSTD_DECOMPRESS_TO_BYTES(ZSTD_COMPRESS({payload}), size_limit => 4096)) AS should_not_return
