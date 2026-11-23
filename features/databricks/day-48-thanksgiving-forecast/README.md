# Day 48 · Databricks · Thanksgiving-week demand with ai_forecast version 2

**New platform feature:** **`ai_forecast` version 2** (GA per the Databricks platform release notes, **2026-11-18**):
`version => '2'` switches `ai_forecast` to a research-optimized time series foundation model and adds built-in
holidays (`holiday_region`), external covariates (`covariate_col`) and non-negative forecasts (`positive_only`).
From the [Databricks platform release notes, November 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/november)
([reference: ai_forecast](https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_forecast)).

## What it does
One store's daily units for whole turkeys, cranberry sauce and pumpkin pie from 2024-10-01 to 2026-11-14 (775 days,
two Thanksgivings, a weekly pattern, a promo every five weeks) come from a deterministic generator in
[`forecast.py`](forecast.py). A promo is planned for 2026-11-20..25. Three calls forecast 2026-11-15..30
(Thanksgiving is 11-26):

```sql
SELECT * FROM ai_forecast(
  TABLE(daily), horizon => '2026-12-01', time_col => 'ds', value_col => 'units', group_col => 'sku',
  frequency => 'D', holiday_region => 'US', positive_only => true, covariate_col => 'promo', version => '2')
```

| Variant | Extra arguments |
|---|---|
| `plain` | none (version 2 on history only) |
| `holiday` | `holiday_region => 'US'`, `positive_only => true` |
| `promo` | the above + `covariate_col => 'promo'` (future rows carry the planned promo with NULL units) |

Because the data are generated, the 16 held-out days are known: each variant is scored with WAPE (sum of absolute
errors / sum of actuals; zero days don't blow it up) over the horizon and over Thanksgiving week, next to two local
baselines.

**Must hold** (else the cloud run fails): every variant returns 16 days x 3 SKUs with `units_forecast`,
`units_lower`, `units_upper`, `lower <= forecast <= upper`, and with `positive_only` no negative forecast or lower
bound. Accuracy is reported, not asserted (the model isn't reproducible locally).

## Why it matters
Thanksgiving demand is a spike a week long, and turkeys drop to almost nothing right after it: a forecast that
ignores the holiday under-orders the peak, and one without a floor predicts negative turkeys for the Friday. Version 2
handles both in one SQL call, and the planned promo goes in as a future covariate instead of a manual uplift.

## How to run
```bash
python features/databricks/day-48-thanksgiving-forecast/forecast.py              # generator + baselines, writes result.json
python features/databricks/day-48-thanksgiving-forecast/forecast.py --sql promo  # the SQL for one variant
pytest -q tests/test_day48_databricks_ai_forecast_v2.py                          # no credentials needed
bash features/databricks/day-48-thanksgiving-forecast/cloud.sh                   # GitHub Actions
```

## Cost
Three statements on the Free Edition serverless SQL warehouse (2,325 inlined rows each, 48 forecast rows). Free
Edition has no bill; on a paid workspace this is a few cents of warehouse time plus AI Functions usage. Read-only.
Requires a Pro or Serverless SQL warehouse; some workspaces need "AI Forecast V2" turned on on the Previews page.

## Result
Local (generator and baselines): 2,325 history rows, 48 held-out rows, 745 actual units over the horizon; turkeys
peak at 80 on 11-25. Baselines: same weekday last week WAPE 0.726 (Thanksgiving week 0.823); last Thanksgiving's
days scaled to this year's level WAPE 0.188 (0.218). Figures in [`result.json`](result.json).

**Cloud:** not run yet. The Free Edition workspace has refused all compute since 2026-10-24 ("organization cancelled
or not active"); `cloud.sh` reports that as a warning and the feature stays `built` until the workspace is active.
