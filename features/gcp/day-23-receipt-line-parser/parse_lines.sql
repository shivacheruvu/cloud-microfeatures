-- Day 23 (Google Cloud): split till-receipt lines into fields with REGEXP_EXTRACT_GROUPS (Preview, 2026-10-26),
-- one call per pattern returning every capturing group, instead of one REGEXP_EXTRACT per field.
-- The lines placeholder below becomes the receipt lines as an ARRAY<STRUCT<receipt STRING, line_no INT64, line STRING>>.
SELECT receipt,
       line_no,
       TO_JSON_STRING(REGEXP_EXTRACT_GROUPS(line, r'{packed}')) AS packed_groups,
       TO_JSON_STRING(REGEXP_EXTRACT_GROUPS(line, r'{weighed}')) AS weighed_groups
FROM UNNEST({lines})
ORDER BY receipt, line_no
