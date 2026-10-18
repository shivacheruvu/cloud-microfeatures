-- Day 13 (Google Cloud): store KPIs that need two levels of aggregation, in one GROUP BY with BigQuery's
-- multi-level aggregation (Preview, 2026-07-08): an aggregate takes another aggregate plus its own GROUP BY.
--   AVG(SUM(revenue) GROUP BY sale_date)             average daily revenue
--   MAX(SUM(revenue) GROUP BY sale_date)             best day
--   AVG(SUM(IF(is_weekend, revenue, NULL))
--       GROUP BY sale_date)                          average weekend-day revenue (weekday sums are NULL, and
--                                                    AVG skips them. BigQuery rejects an inner HAVING next to
--                                                    GROUP BY in AVG, see README)
--   AVG(SUM(revenue) GROUP BY basket_id)             average basket value
--   AVG(ANY_VALUE(shelf_price) GROUP BY sku)         average shelf price, once per SKU despite the join
-- Basket lines and shelf prices are inlined below (about 50 KB), so no table is created or read.
WITH lines AS (
  SELECT * FROM UNNEST([
    {lines}
  ])
), prices AS (
  SELECT * FROM UNNEST([
    {prices}
  ])
), sales AS (
  SELECT l.store, l.sale_date, EXTRACT(DAYOFWEEK FROM l.sale_date) IN (1, 7) AS is_weekend,
         l.basket_id, l.sku, l.qty, p.shelf_price, l.qty * p.shelf_price AS revenue
  FROM lines AS l JOIN prices AS p USING (store, sku)
)
SELECT
  store,
  SUM(revenue) AS revenue,
  AVG(SUM(revenue) GROUP BY sale_date) AS avg_daily_revenue,
  MAX(SUM(revenue) GROUP BY sale_date) AS best_day_revenue,
  AVG(SUM(IF(is_weekend, revenue, NULL)) GROUP BY sale_date) AS avg_weekend_day_revenue,
  AVG(SUM(revenue) GROUP BY basket_id) AS avg_basket_value,
  AVG(SUM(qty) GROUP BY basket_id) AS avg_items_per_basket,
  AVG(ANY_VALUE(shelf_price) GROUP BY sku) AS avg_shelf_price,
  AVG(shelf_price) AS naive_avg_shelf_price
FROM sales
GROUP BY store
ORDER BY store
