-- Day 18 Google Cloud: weekly-ad deal finder. {table}, {items}, {min_saving} and {max_per_kg} are filled in by
-- shelf_tags.py. DIV is BigQuery integer division (a DuckDB macro locally); all values are positive.

-- name: setup
CREATE TABLE {table}
OPTIONS (description = 'Weekly ad items with shelf prices (made-up)', expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR))
AS SELECT * FROM UNNEST(ARRAY<STRUCT<sku STRING, product STRING, aisle STRING, shelf_cents INT64, ad_pct INT64, pack_grams INT64, tax_bp INT64>>[
  {items}
]);

-- name: aliases
-- With reusable column aliases: each step of the shelf-tag math is written once and reused by name.
SELECT
  sku, product, aisle, shelf_cents,
  DIV(shelf_cents * (100 - ad_pct) + 50, 100)              AS promo_cents,
  shelf_cents - promo_cents                                AS saving_cents,
  DIV(promo_cents * 1000 + DIV(pack_grams, 2), pack_grams) AS cents_per_kg,
  DIV(promo_cents * (10000 + tax_bp) + 5000, 10000)        AS checkout_cents
FROM {table}
WHERE saving_cents >= {min_saving}
  AND cents_per_kg <= {max_per_kg}
ORDER BY cents_per_kg, sku;

-- name: expanded
-- The same query without the feature: the promo-price expression is repeated everywhere it is needed.
SELECT
  sku, product, aisle, shelf_cents,
  DIV(shelf_cents * (100 - ad_pct) + 50, 100) AS promo_cents,
  shelf_cents - DIV(shelf_cents * (100 - ad_pct) + 50, 100) AS saving_cents,
  DIV(DIV(shelf_cents * (100 - ad_pct) + 50, 100) * 1000 + DIV(pack_grams, 2), pack_grams) AS cents_per_kg,
  DIV(DIV(shelf_cents * (100 - ad_pct) + 50, 100) * (10000 + tax_bp) + 5000, 10000) AS checkout_cents
FROM {table}
WHERE shelf_cents - DIV(shelf_cents * (100 - ad_pct) + 50, 100) >= {min_saving}
  AND DIV(DIV(shelf_cents * (100 - ad_pct) + 50, 100) * 1000 + DIV(pack_grams, 2), pack_grams) <= {max_per_kg}
ORDER BY cents_per_kg, sku;
