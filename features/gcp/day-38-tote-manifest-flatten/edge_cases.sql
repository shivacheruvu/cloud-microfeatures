-- Day 38 (Google Cloud): JSON_FLATTEN edge cases, reported next to the local model (informational, not required).
-- The first five are the documented examples; the rest are cases the docs don't spell out.
SELECT label, TO_JSON_STRING(JSON_FLATTEN(j)) AS flattened
FROM UNNEST([
  STRUCT('doc: scalar' AS label, JSON '1' AS j),
  ('doc: array with null', JSON '[1, 2, null]'),
  ('doc: nested arrays', JSON '[[[1]], 2, [3]]'),
  ('doc: array inside object', JSON '{"a": [[1]]}'),
  ('doc: mixed', JSON '[[[1, 2], 3], {"a": 4}, true]'),
  ('empty array', JSON '[]'),
  ('arrays of empty arrays', JSON '[[], [[]], [1]]'),
  ('JSON null', JSON 'null'),
  ('SQL NULL', CAST(NULL AS JSON))
])
ORDER BY label
