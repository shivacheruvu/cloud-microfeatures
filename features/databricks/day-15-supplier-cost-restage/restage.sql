-- name: databricks
-- A supplier sends today's cost file, then re-sends a corrected one. Stage each delivery in a session-local temporary
-- table and price against the newest one. CREATE OR REPLACE TEMP TABLE (new in Databricks SQL 2026.15) replaces the
-- staging table in one statement, so the re-delivery needs no DROP and the first file's rows are gone; a second plain
-- CREATE TEMP TABLE would fail with "table already exists".
-- The whole thing is one SQL script (BEGIN ... END), so every statement runs in the same session and the final
-- SELECT is the script's result. Temporary tables vanish when the session ends: nothing is left behind.
-- {shelf} / {delivery1} / {delivery2} are inline VALUES rows built from data/*.csv.
BEGIN
  CREATE OR REPLACE TEMP TABLE shelf (sku STRING, aisle STRING, price_cents INT);
  INSERT INTO shelf VALUES
{shelf};
  CREATE OR REPLACE TEMP TABLE cost_batch (sku STRING, cost_cents INT, delivery INT);
  INSERT INTO cost_batch VALUES
{delivery1};
  CREATE OR REPLACE TEMP TABLE cost_batch (sku STRING, cost_cents INT, delivery INT);
  INSERT INTO cost_batch VALUES
{delivery2};
  SELECT s.sku, s.aisle, c.cost_cents, s.price_cents, c.delivery,
         (s.price_cents - c.cost_cents) * 4 < s.price_cents AS below_target,
         CASE WHEN (s.price_cents - c.cost_cents) * 4 < s.price_cents
              THEN CAST(CEIL(c.cost_cents * 4 / 3.0) AS INT) + 9 - CAST(CEIL(c.cost_cents * 4 / 3.0) AS INT) % 10
         END AS suggested_cents,
         (SELECT COUNT(*) FROM cost_batch) AS batch_rows
  FROM shelf s
  JOIN cost_batch c ON c.sku = s.sku
  ORDER BY s.sku;
END
