-- Day 36 (Google Cloud): archive raw scan exports as Zstandard-compressed BYTES with ZSTD_COMPRESS, and read them
-- back with ZSTD_DECOMPRESS_TO_STRING / ZSTD_DECOMPRESS_TO_BYTES (GoogleSQL compression functions, Preview,
-- 2026-11-09). The exports placeholder below becomes ARRAY<STRUCT<batch STRING, payload STRING>>.
WITH z AS (
  SELECT batch,
         payload,
         ZSTD_COMPRESS(payload, level => -5) AS z_fast,
         ZSTD_COMPRESS(payload, level => 1) AS z_1,
         ZSTD_COMPRESS(payload) AS z_default,
         ZSTD_COMPRESS(payload, level => 3) AS z_3,
         ZSTD_COMPRESS(payload, level => 19) AS z_19,
         ZSTD_COMPRESS(CAST(payload AS BYTES), level => 19) AS z_19_from_bytes
  FROM UNNEST({exports})
)
SELECT batch,
       BYTE_LENGTH(payload) AS raw_bytes,
       CHAR_LENGTH(payload) AS raw_chars,
       TO_BASE64(z_fast) AS z_fast,
       TO_BASE64(z_1) AS z_1,
       TO_BASE64(z_default) AS z_default,
       TO_BASE64(z_3) AS z_3,
       TO_BASE64(z_19) AS z_19,
       z_19 = z_19_from_bytes AS string_and_bytes_input_same,
       ZSTD_DECOMPRESS_TO_STRING(z_fast) = payload
         AND ZSTD_DECOMPRESS_TO_STRING(z_1) = payload
         AND ZSTD_DECOMPRESS_TO_STRING(z_default) = payload
         AND ZSTD_DECOMPRESS_TO_STRING(z_19) = payload AS string_round_trip,
       ZSTD_DECOMPRESS_TO_BYTES(z_3) = CAST(payload AS BYTES) AS bytes_round_trip,
       ZSTD_DECOMPRESS_TO_BYTES(z_19, size_limit => 1048576) = CAST(payload AS BYTES) AS round_trip_under_1mib_limit
FROM z
ORDER BY batch
