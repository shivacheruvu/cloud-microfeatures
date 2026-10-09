-- Governed store-sales KPIs as a Unity Catalog metric view, with two window measures (GA 2026-10-06).
--   units_prior_7d : trailing 7 day (exclusive by default) = the 7 calendar days before each day, not the day itself
--   open_days_prior_7d : trading days in that same window (a closed day doesn't drag the average down)
--   units_mtd      : cumulative = everything up to and including the day (the data is one month, so month-to-date)
CREATE OR REPLACE VIEW {schema}.store_sales_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: Daily grocery units per store with rolling and cumulative window measures
source: {schema}.daily_units
fields:
  - name: sale_date
    expr: sale_date
  - name: store
    expr: store
measures:
  - name: units_sold
    expr: SUM(units)
  - name: units_prior_7d
    expr: SUM(units)
    window:
      - order: sale_date
        range: trailing 7 day
        semiadditive: last
  - name: open_days_prior_7d
    expr: COUNT(DISTINCT sale_date)
    window:
      - order: sale_date
        range: trailing 7 day
        semiadditive: last
  - name: units_mtd
    expr: SUM(units)
    window:
      - order: sale_date
        range: cumulative
        semiadditive: last
$$
