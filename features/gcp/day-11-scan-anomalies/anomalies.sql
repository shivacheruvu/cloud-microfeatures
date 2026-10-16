-- Daily scan anomalies for three staples at one store with TimesFM 3.0 (Preview, 2026-10-07) in AI.DETECT_ANOMALIES.
-- New with TimesFM 3.0: the model can be used for anomaly detection, not only forecasting. Three series (id_cols)
-- in one call: 8 weeks of history, 4 weeks of target days to check. No model or dataset is created.
WITH scan_history AS (
  SELECT * FROM UNNEST([
    {history}
  ])
), scan_target AS (
  SELECT * FROM UNNEST([
    {target}
  ])
)
SELECT *
FROM AI.DETECT_ANOMALIES(
  TABLE scan_history,
  TABLE scan_target,
  data_col => 'units',
  timestamp_col => 'day',
  id_cols => ['product'],
  model => '{model}',
  anomaly_prob_threshold => {threshold}
)
