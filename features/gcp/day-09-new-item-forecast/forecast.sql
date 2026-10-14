-- Weekly units for 8 new grocery items, zero-shot: AI.PREDICT (Preview) hands the 48 known items to TabFM as
-- context and predicts the label for the new ones. No model is created; the data is inlined, so nothing to clean up.
-- sku is only in the prediction query (an extra column), so it isn't used as a feature.
SELECT *
FROM AI.PREDICT(
  (SELECT * FROM UNNEST([
      {known}
  ])),
  (SELECT * FROM UNNEST([
      {new}
  ])),
  label_col => 'weekly_units')
ORDER BY sku
