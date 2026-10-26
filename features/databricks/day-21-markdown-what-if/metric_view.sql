-- Markdown what-if KPIs as a Unity Catalog metric view with PARAMETERS (Public Preview, 2026-06-26).
-- One definition answers "what if this week's lines had sold at X% off?" for any X: the caller binds `markdown`
-- at query time (FROM view(markdown => 0.2)); no separate view per markdown level.
--   net_sales        : sales at the marked-down price
--   margin           : (marked-down price - unit cost) x units
--   lines_below_cost : products whose marked-down price is under unit cost (never mark these down that far)
CREATE OR REPLACE VIEW {schema}.markdown_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: Weekly grocery sales with a query-time markdown parameter
parameters:
  - name: markdown
    data_type: double
    default: 0
source: {schema}.week_sales
fields:
  - name: department
    expr: department
measures:
  - name: units
    expr: SUM(units)
  - name: net_sales
    expr: SUM(ROUND((1 - markdown) * shelf_price, 2) * units)
  - name: margin
    expr: SUM((ROUND((1 - markdown) * shelf_price, 2) - unit_cost) * units)
  - name: lines_below_cost
    expr: COUNT_IF(ROUND((1 - markdown) * shelf_price, 2) < unit_cost)
$$
