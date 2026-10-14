-- Parse every saved report query with the new `parse_sql` (Databricks SQL 2026.36). It only parses: nothing is
-- executed and none of the referenced tables have to exist. The JSON it returns carries statement metadata, table
-- references and select-list expressions, or the parser error. {reports} is an inline VALUES list built from
-- data/reports.csv.
SELECT name, CAST(parse_sql(query) AS STRING) AS parsed
FROM VALUES
{reports}
AS t(name, query)
ORDER BY name;
