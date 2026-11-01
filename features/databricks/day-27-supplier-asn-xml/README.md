# Day 27 · Databricks · Supplier delivery notes in XML: native XML with a rescued data column

**New platform feature:** **native XML file format support** (GA): `read_files`, Auto Loader and `COPY INTO` read
XML directly (`format => 'xml'`, `rowTag`), with `from_xml` / `schema_of_xml` in SQL and a rescued data column for
values that don't fit the schema. Announced as generally available in the
[Databricks platform release notes, October 2026](https://docs.databricks.com/aws/en/release-notes/product/2026/october)
on **2026-10-29** ([docs: Read and write XML files](https://docs.databricks.com/aws/en/query/formats/xml)).

## What it does
Three suppliers send advance shipping notices (ASNs) as XML, one `<delivery>` per file with a `<line>` per product
([`data/`](data/)). [`receipts.sql`](receipts.sql) reads every line of every file in one query:

```sql
SELECT _metadata.file_name AS file, _no AS line_no, sku, qty, unit_cost, _rescued
FROM read_files('/Volumes/<catalog>/<schema>/asn/', format => 'xml', rowTag => 'line',
  schema => '_no INT, sku STRING, qty INT, unit_cost DECIMAL(8,2)', rescuedDataColumn => '_rescued')
```

The schema is explicit (receiving needs integers and money, not guesses), and the `line` attribute `no` arrives as
`_no` (default attribute prefix). The suppliers don't all follow it: produce sends `qty` `2 cs` on one line and adds a
`<lot>` element on another; the bakery writes `3,49` with a decimal comma. Without a rescued column those values
would just become NULL (or vanish). With it, each one is kept in `_rescued` as JSON next to its line.

## Why it matters
Supplier XML (EDI-style ASNs, invoices) no longer needs a separate parser or a JVM library on the cluster, and a
receiving clerk can see exactly which supplier line didn't fit and why, instead of a silently short delivery total.

## How to run
```bash
python features/databricks/day-27-supplier-asn-xml/asn_xml.py      # local, writes result.json
pytest -q tests/test_day27_databricks_native_xml.py
```
The local run is a pure-Python reference with the same rules (explicit schema casts; misfit values and fields not in
the schema go to `_rescued`). `cloud.sh` (GitHub Actions) creates a scratch schema `microfeatures_day27` with a
managed volume, uploads the three files through the Files API, runs `receipts.sql` on a serverless SQL warehouse
through the Statement Execution API, and checks every line and every rescued value equal the local run (the
`_file_path` key Databricks adds to the rescued JSON is ignored). The volume and schema are deleted on exit. If the
workspace refuses compute (as it has since 2026-10-24: account inactive), the run warns and nothing is left behind.
Works on Free Edition (serverless SQL warehouse).

## Cost
One small query on the Free Edition serverless warehouse, three files under 1 KB. $0.

## Result
Local: 16 lines from 3 delivery notes; 14 priced, **3 lines kept their misfit values in `_rescued`**
(`ASN-P-1027` line 3 `qty: "2 cs"`, line 5 `lot: "L-2231"`, `ASN-B-1027` line 2 `unit_cost: "3,49"`); 2 of them
would otherwise have been silent NULLs.

| Supplier | Lines | Priced | Units | Value | Rescued |
|---|---|---|---|---|---|
| Prairie Dairy Co-op | 6 | 6 | 140 | 361.88 | 0 |
| Riverbend Produce | 6 | 5 | 202 | 193.76 | 2 |
| Hearth Street Bakery | 4 | 3 | 52 | 122.45 | 1 |

Cloud result: see the day's report. Full numbers in [`result.json`](result.json).
