-- Fallback for classification.sql: the same question with STRING labels instead of BOOL.
-- (The BOOL form hit a persistent BigQuery internal error on 2026-10-06.) STRING labels are macro-averaged
-- over both labels; for two labels, accuracy is the same and precision / recall / F1 are the mean of the two classes.
SELECT *
FROM ML.METRICS(
  (SELECT IF(organic, 'healthy', 'other') AS predicted, IF(health_score >= 70, 'healthy', 'other') AS actual
   FROM `{table}`),
  predicted_col => 'predicted',
  actual_col => 'actual',
  task_type => 'classification'
)
