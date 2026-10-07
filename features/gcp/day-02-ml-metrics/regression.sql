-- How well does the Nutri-Score letter alone estimate the 0-100 health score?
-- New in BigQuery (Preview, 2026-09-10): ML.METRICS with task_type 'regression' on plain columns.
SELECT *
FROM ML.METRICS(
  (SELECT
     CAST(CASE nutri_grade WHEN 'a' THEN 90 WHEN 'b' THEN 75 WHEN 'c' THEN 55 WHEN 'd' THEN 38 ELSE 22 END AS FLOAT64)
       AS predicted,
     CAST(health_score AS FLOAT64) AS actual
   FROM `{table}`),
  predicted_col => 'predicted',
  actual_col => 'actual',
  task_type => 'regression'
)
