-- Day 36 (Databricks): the daily markdown-planning query. Every date comes in as a job parameter resolved by date
-- arithmetic in dynamic value references (Beta, 2026-11-09), e.g. sell_by_cutoff = {{(job.start_time + 3.days).iso_date}},
-- instead of current_date() maths inside the SQL, so a re-run or backfill of a past run uses that run's dates.
-- Parameters: :run_date, :yesterday, :yesterday_last_week, :week_start, :sell_by_cutoff. ANSI SQL (DuckDB locally).
WITH velocity AS (            -- average units a day over the 7 days before the run
  SELECT store, sku, SUM(units) / 7.0 AS per_day
  FROM sales
  WHERE sale_date >= CAST(:week_start AS DATE) AND sale_date < CAST(:run_date AS DATE)
  GROUP BY store, sku
),
lots_due AS (
  SELECT l.store, l.sku, l.lot, l.sell_by, l.on_hand,
         CAST(l.sell_by - CAST(:run_date AS DATE) AS INTEGER) AS days_left,
         COALESCE(v.per_day, 0) AS per_day
  FROM lots l LEFT JOIN velocity v ON v.store = l.store AND v.sku = l.sku
  WHERE l.sell_by <= CAST(:sell_by_cutoff AS DATE)
)
SELECT store, sku, lot, CAST(sell_by AS VARCHAR) AS sell_by, on_hand, days_left,
       ROUND(per_day, 2) AS per_day,
       CAST(FLOOR(per_day * GREATEST(days_left, 0)) AS INTEGER) AS expected_to_sell,
       CASE WHEN days_left < 0 THEN 'pull'
            WHEN on_hand <= FLOOR(per_day * days_left) THEN 'keep'
            WHEN days_left <= 1 THEN '50% off'
            ELSE '25% off' END AS action
FROM lots_due
ORDER BY store, sell_by, sku, lot
