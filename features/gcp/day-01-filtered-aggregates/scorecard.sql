-- Grocery category scorecard in one pass.
-- New in BigQuery (Preview, 2026-09-14): a WHERE clause inside an aggregate call filters that
-- aggregate's input, e.g. AVG(health_score WHERE organic), so several filtered metrics share one scan
-- without CASE WHEN tricks or self-joins.
SELECT
  category,
  COUNT(barcode) AS products,
  COUNT(barcode WHERE nutri_grade IN ('a', 'b')) AS grade_a_or_b,
  COUNT(barcode WHERE high_risk_additives > 0) AS with_high_risk_additives,
  ROUND(AVG(health_score), 1) AS avg_score,
  ROUND(AVG(health_score WHERE organic), 1) AS avg_score_organic,
  ROUND(AVG(health_score WHERE NOT organic), 1) AS avg_score_conventional
FROM `{table}`
GROUP BY category
ORDER BY avg_score DESC, category
