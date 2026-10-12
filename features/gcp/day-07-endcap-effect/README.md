# Day 7 · Google Cloud · Did the shelf reset move sales? AI.CAUSAL_EFFECT

**New platform feature:** [`AI.CAUSAL_EFFECT`](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-causal-effect),
**Preview, 2026-09-10** ([BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes)).
One table-valued function call quantifies the effect of an intervention on time series: it fits `ARIMA_PLUS` to each
series before the intervention date, forecasts the counterfactual after it, and returns `p_value`,
`prob_causal_effect`, `absolute_effect` and `relative_effect` per series. No model to create, no control series.

## What it does
On 2026-09-01 a store reset its shelves: **kombucha** moved to an endcap, **oat milk** moved to the bottom shelf, and
**sparkling water** stayed where it was. 84 days of daily units before the reset and 28 after, per product, with a
strong weekend pattern (`data/daily_units.csv`, generated deterministically by `causal.py --make-data`).

```sql
SELECT *
FROM AI.CAUSAL_EFFECT(
  TABLE daily,
  data_col => 'units',
  timestamp_col => 'sale_date',
  intervention_timestamp => '2026-09-01',
  id_cols => ['product']
)
```

Locally, the counterfactual for each post-reset day is the mean of the same weekday over the 12 weeks before; the
summed difference is tested against the spread of the pre-period's residuals (two-tailed, normal approximation).
p < 0.05 means "lift" or "drop", otherwise "no effect".

## Why it matters
"Did the endcap work?" is the question every promo review asks, and comparing averages before and after mixes in the
weekly pattern and noise. `AI.CAUSAL_EFFECT` gives a counterfactual and a p-value per product in one SQL call, so a
store manager's list of shelf changes can be checked in bulk, including the ones that did nothing.

## How to run
```bash
python causal.py --local          # pure Python; writes result.json
python causal.py --sql            # the BigQuery query with the data inlined
pytest -q tests/test_day07_gcp_causal_effect.py
```
`cloud.sh` runs the query in GitHub Actions (keyless) and fails on a non-empty `status`, a missing product, or a
different effect class than local; a relative effect off by more than max(5 points, 35%) is a warning. Nothing is
created in the project, so there is nothing to clean up.

## Cost
One query over a few KB of inlined data, capped at 20 MiB billed: inside the free tier.

## Result (local)
| Product | Class | Relative effect | Absolute effect (28 days) | p |
|---|---|---|---|---|
| kombucha 16oz | lift | +42.5% | +352 units | < 0.0001 |
| oat milk 64oz | drop | -29.5% | -371 units | < 0.0001 |
| sparkling water 12pk | no effect | -0.2% | -2 units | 0.82 |

Full numbers in [`result.json`](result.json).
