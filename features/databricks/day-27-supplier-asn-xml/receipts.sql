-- name: asn_lines (one row per <line> in every supplier delivery note; {path} is the volume folder)
SELECT _metadata.file_name AS file,
       _no                 AS line_no,
       sku,
       qty,
       unit_cost,
       _rescued
FROM read_files(
  '{path}',
  format => 'xml',
  rowTag => 'line',
  schema => '_no INT, sku STRING, qty INT, unit_cost DECIMAL(8,2)',
  rescuedDataColumn => '_rescued'
)
ORDER BY file, line_no
