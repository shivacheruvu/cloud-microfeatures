-- Weekly store-sales KPIs as a Unity Catalog metric view with window measures on a NUMERIC index column
-- (released 2026-09-14). week_index is a dense integer fiscal-week number (1, 2, 3, ... across the fiscal-year
-- boundary), so "last week" and "the 4 weeks before" are unitless steps, not calendar intervals:
--   units_prev_week : range current, offset -1  = the week before (FY2027 week 1 -> FY2026 week 52)
--   units_prior_4wk : range trailing 4           = the 4 weeks before, not the week itself (exclusive default)
--   units_to_date   : range cumulative           = everything up to and including the week
CREATE OR REPLACE VIEW {schema}.weekly_sales_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: Weekly grocery units per store on a dense fiscal-week index, with unitless window measures
source: {schema}.weekly_units
fields:
  - name: week_index
    expr: week_index
  - name: store
    expr: store
measures:
  - name: units_sold
    expr: SUM(units)
  - name: units_prev_week
    expr: SUM(units)
    window:
      - order: week_index
        range: current
        offset: -1
        semiadditive: last
  - name: units_prior_4wk
    expr: SUM(units)
    window:
      - order: week_index
        range: trailing 4
        semiadditive: last
  - name: units_to_date
    expr: SUM(units)
    window:
      - order: week_index
        range: cumulative
        semiadditive: last
$$
