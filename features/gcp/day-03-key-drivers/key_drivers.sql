-- What drove this week's change in units sold, by store, category and channel?
-- New in BigQuery (GA, 2026-09-29): AI.KEY_DRIVERS compares an interest group (this week) with a reference group
-- (last week) and ranks every segment by how much of the change it explains. No model, no Gemini call.
SELECT
  drivers,
  metric_interest,
  metric_reference,
  difference,
  unexpected_difference,
  apriori_support,
  contribution
FROM AI.KEY_DRIVERS(
  (SELECT store, category, channel, CAST(units AS BIGNUMERIC) AS units, week = 'this_week' AS is_this_week
   FROM UNNEST([
{values}
   ])),
  metric_col => 'units',
  dimension_cols => ['store', 'category', 'channel'],
  interest_label_col => 'is_this_week',
  min_apriori_support => 0,
  enable_pruning => FALSE
)
ORDER BY contribution DESC
