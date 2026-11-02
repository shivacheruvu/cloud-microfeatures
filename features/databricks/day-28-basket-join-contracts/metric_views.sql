-- Three Unity Catalog metric views over the scratch tables, then the joins between them declared as relationships
-- (Beta, 2026-10-30). Relationships are metadata only: Databricks records the cardinality and the join keys but
-- doesn't check the data, so contracts.py audits each one against the rows. Statements are separated by a line ";".
CREATE OR REPLACE VIEW {schema}.basket_mv
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: Basket lines (one row per scanned line)
source: {schema}.basket_lines
fields:
  - name: store_id
    expr: store_id
  - name: barcode
    expr: barcode
measures:
  - name: row_count
    expr: COUNT(1)
  - name: revenue
    expr: SUM(qty * unit_price)
$$
;
CREATE OR REPLACE VIEW {schema}.products_mv
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: Product master (should be one row per barcode)
source: {schema}.products
fields:
  - name: barcode
    expr: barcode
  - name: department
    expr: department
measures:
  - name: row_count
    expr: COUNT(1)
$$
;
CREATE OR REPLACE VIEW {schema}.stores_mv
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: Store master (one row per store)
source: {schema}.stores
fields:
  - name: store_id
    expr: store_id
  - name: city
    expr: city
measures:
  - name: row_count
    expr: COUNT(1)
$$
;
ALTER VIEW {schema}.basket_mv ADD CONSTRAINT basket_product
  MANY TO ONE RELATIONSHIP TO {schema}.products_mv
  USING (barcode)
;
ALTER VIEW {schema}.basket_mv ADD CONSTRAINT basket_store
  MANY TO ONE RELATIONSHIP TO {schema}.stores_mv
  USING (store_id)
