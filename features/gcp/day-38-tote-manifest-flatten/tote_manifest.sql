-- Day 38 (Google Cloud): count every picked item in nested tote manifests with JSON_FLATTEN (GA, 2026-11-11).
-- The orders placeholder below is replaced by tote_manifest.py with an inline ARRAY<STRUCT<order_id STRING, doc STRING>>: nothing is read
-- from a table, so the query scans 0 bytes.
WITH orders AS (
  SELECT order_id, PARSE_JSON(doc) AS doc
  FROM UNNEST({orders})
),
-- New way: JSON_FLATTEN returns every non-array value under any number of nested arrays, so the picker's packing
-- depth (tote / bag / bag-in-bag / no bag at all) no longer matters. Objects are kept whole: an item's `subs` array
-- stays inside the item and is not counted as extra items.
flat AS (
  SELECT o.order_id, pos, item
  FROM orders AS o, UNNEST(JSON_FLATTEN(JSON_QUERY(o.doc, '$.totes'))) AS item WITH OFFSET AS pos
),
-- Old way: assume a fixed depth tote -> bag -> item and unnest three times with JSON_QUERY_ARRAY. An element that is
-- not an array at the expected level returns NULL and silently disappears; a deeper array is not an item.
fixed AS (
  SELECT o.order_id, item
  FROM orders AS o,
    UNNEST(JSON_QUERY_ARRAY(o.doc, '$.totes')) AS tote,
    UNNEST(IF(JSON_TYPE(tote) = 'array', JSON_QUERY_ARRAY(tote), ARRAY<JSON>[])) AS bag,  -- a non-array would be NULL anyway
    UNNEST(IF(JSON_TYPE(bag) = 'array', JSON_QUERY_ARRAY(bag), ARRAY<JSON>[])) AS item
  WHERE JSON_TYPE(item) = 'object'
),
flat_agg AS (
  SELECT order_id,
    COUNT(*) AS flat_items,
    SUM(LAX_INT64(item.qty)) AS flat_units,
    SUM(LAX_INT64(item.qty) * LAX_INT64(item.price_cents)) AS flat_value_cents,
    COUNTIF(JSON_TYPE(item) != 'object') AS flat_non_objects,
    COUNTIF(item.subs IS NOT NULL) AS items_with_subs,
    STRING_AGG(JSON_VALUE(item.sku), ',' ORDER BY pos) AS sku_sequence
  FROM flat GROUP BY order_id
),
fixed_agg AS (
  SELECT order_id,
    COUNT(*) AS fixed_items,
    SUM(LAX_INT64(item.qty)) AS fixed_units,
    SUM(LAX_INT64(item.qty) * LAX_INT64(item.price_cents)) AS fixed_value_cents
  FROM fixed GROUP BY order_id
)
SELECT
  o.order_id,
  JSON_VALUE(o.doc.store) AS store,
  JSON_QUERY(o.doc, '$.totes') IS NULL AS no_totes,
  COALESCE(f.flat_items, 0) AS flat_items,
  COALESCE(f.flat_units, 0) AS flat_units,
  COALESCE(f.flat_value_cents, 0) AS flat_value_cents,
  COALESCE(f.flat_non_objects, 0) AS flat_non_objects,
  COALESCE(f.items_with_subs, 0) AS items_with_subs,
  COALESCE(f.sku_sequence, '') AS sku_sequence,
  COALESCE(x.fixed_items, 0) AS fixed_items,
  COALESCE(x.fixed_units, 0) AS fixed_units,
  COALESCE(x.fixed_value_cents, 0) AS fixed_value_cents
FROM orders AS o
LEFT JOIN flat_agg AS f USING (order_id)
LEFT JOIN fixed_agg AS x USING (order_id)
ORDER BY o.order_id
