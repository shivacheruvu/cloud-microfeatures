-- Does the "organic" label predict a healthy product (health score >= 70)?
-- New in BigQuery (Preview, 2026-09-10): ML.METRICS scores any predicted-vs-actual columns without a model.
-- BOOL columns give binary precision / recall / accuracy / F1 for the TRUE class.
SELECT *
FROM ML.METRICS(
  (SELECT organic AS predicted, health_score >= 70 AS actual FROM `{table}`),
  predicted_col => 'predicted',
  actual_col => 'actual',
  task_type => 'classification'
)
