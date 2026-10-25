-- name: create_products
-- Catalog: a surrogate primary key plus an informational UNIQUE constraint on the barcode (Public Preview,
-- Databricks SQL / DBR 18.2+). The barcode is nullable: loose produce has no UPC, and NULLs never clash.
CREATE TABLE {schema}.products (
  product_id BIGINT NOT NULL,
  upc        STRING,
  name       STRING,
  CONSTRAINT products_pk PRIMARY KEY (product_id),
  CONSTRAINT products_upc_uq UNIQUE (upc)
);

-- name: create_scans
-- Register scans carry only the barcode, so the foreign key references the UNIQUE key, not the primary key.
CREATE TABLE {schema}.scans (
  scan_id BIGINT NOT NULL,
  upc     STRING,
  qty     INT,
  price   DECIMAL(6, 2),
  CONSTRAINT scans_pk PRIMARY KEY (scan_id),
  CONSTRAINT scans_upc_fk FOREIGN KEY (upc) REFERENCES {schema}.products (upc)
);

-- name: duplicate_upcs
-- Rows that break products_upc_uq. Databricks does not enforce it, so this check is the enforcement.
SELECT upc, count(*) AS listings, min(product_id) AS first_id, max(product_id) AS last_id
FROM {schema}.products
WHERE upc IS NOT NULL
GROUP BY upc
HAVING count(*) > 1
ORDER BY upc;

-- name: orphan_scans
-- Rows that break scans_upc_fk: a barcode the catalog doesn't know. A NULL barcode (weighed produce) is allowed.
SELECT s.scan_id, s.upc
FROM {schema}.scans s
LEFT JOIN {schema}.products p ON s.upc = p.upc
WHERE s.upc IS NOT NULL AND p.upc IS NULL
ORDER BY s.scan_id;

-- name: join_fanout
-- What a duplicate barcode costs a sales report that joins scans to the catalog on the barcode.
SELECT
  (SELECT count(*) FROM {schema}.scans WHERE upc IS NOT NULL)                         AS barcode_scans,
  (SELECT count(*) FROM {schema}.scans s JOIN {schema}.products p ON s.upc = p.upc)  AS joined_rows,
  (SELECT CAST(sum(qty * price) AS DECIMAL(10, 2)) FROM {schema}.scans)              AS true_sales,
  (SELECT CAST(sum(s.qty * s.price) AS DECIMAL(10, 2))
     FROM {schema}.scans s LEFT JOIN {schema}.products p ON s.upc = p.upc)           AS joined_sales;

-- name: declared_constraints
-- Databricks only: what Unity Catalog recorded, read back from the information schema.
SELECT table_name, constraint_name, constraint_type
FROM information_schema.table_constraints
WHERE table_schema = '{schema}'
ORDER BY table_name, constraint_name;

-- name: fk_targets
-- Databricks only: the foreign key points at the UNIQUE constraint, not at the primary key.
SELECT constraint_name, unique_constraint_name
FROM information_schema.referential_constraints
WHERE constraint_schema = '{schema}'
ORDER BY constraint_name;
