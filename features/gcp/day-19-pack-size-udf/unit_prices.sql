-- Day 19 Google Cloud: shelf unit prices from free-text pack sizes, using the parse_pack Python UDF (GA 2026-05-20).
-- {fn} is the UDF (`project.dataset.parse_pack` in BigQuery), {table} the items table. value_rank 1 = best value in
-- its category for that unit; labels the UDF can't read get NULLs and no rank.
WITH parsed AS (
  SELECT category, sku, label, price_cents, {fn}(label) AS pack
  FROM {table}
),
priced AS (
  SELECT category, sku, label, price_cents, pack.base_qty AS base_qty, pack.unit AS unit,
         CASE pack.unit WHEN 'ct' THEN 1 ELSE 100 END AS per,
         price_cents * 10.0 * (CASE pack.unit WHEN 'ct' THEN 1 ELSE 100 END) / pack.base_qty AS raw
  FROM parsed
)
SELECT category, sku, label, price_cents, base_qty, unit, per,
       CAST(FLOOR(raw + 0.5) AS INT64) AS tenth_cents_per,
       CASE WHEN unit IS NULL THEN NULL
            ELSE ROW_NUMBER() OVER (PARTITION BY category, unit ORDER BY raw, sku) END AS value_rank
FROM priced
ORDER BY category, unit, value_rank, sku
