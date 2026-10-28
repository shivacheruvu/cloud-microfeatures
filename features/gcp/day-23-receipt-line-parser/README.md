# Day 23 · Google Cloud · Receipt lines into fields with one regex call: REGEXP_EXTRACT_GROUPS

**New platform feature:** **`REGEXP_EXTRACT_GROUPS`** (Preview), a GoogleSQL string function that returns the
substrings matched by *every* capturing group of a regular expression. Announced in the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) on **2026-10-26**
([reference](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/string_functions#regexp_extract_groups)).

## What it does
Till receipts from the scan exports arrive as text lines. Two patterns cover nearly all of them:

| Kind | Example | Groups |
|---|---|---|
| packed | `OAT MILK 64OZ 2 @ 3.49` | name, pack size, unit, quantity, price each (size + unit optional) |
| weighed | `BANANAS 2.31 LB @ 0.59/LB` | name, weight, price per lb |

```sql
SELECT receipt, line_no,
       TO_JSON_STRING(REGEXP_EXTRACT_GROUPS(line, r'^(.+?)(?: (\d+(?:\.\d+)?) ?(OZ|LB|CT|GAL|L|KG|G))? (\d+) @ (\d+\.\d{2})$')) AS packed_groups,
       TO_JSON_STRING(REGEXP_EXTRACT_GROUPS(line, r'^(.+?) (\d+\.\d{2}) LB @ (\d+\.\d{2})/LB$')) AS weighed_groups
FROM UNNEST([...receipt lines...])
```

Lines that match neither (coupons, bag fees) are listed for a person instead of being guessed at. Line amounts
are quantity x price, or weight x price per lb rounded half-up to the cent.

## Why it matters
`REGEXP_EXTRACT` and `REGEXP_EXTRACT_ALL` accept at most one capturing group, so splitting a line into five fields
meant five single-group patterns that had to agree with each other. One pattern with all its groups is shorter,
can't drift, and returns all fields from the same match: here **2 calls per line instead of 8**.

## How to run
```bash
python features/gcp/day-23-receipt-line-parser/receipt_parser.py          # local (Python re); writes result.json
python features/gcp/day-23-receipt-line-parser/receipt_parser.py --sql    # the BigQuery query
pytest -q tests/test_day23_gcp_regexp_extract_groups.py
```
`cloud.sh` (GitHub Actions, keyless) runs the query with the lines inlined (nothing to create or clean up), then
checks every line's fields and amount against the local run and fails on any difference.

**Note:** the function's reference section hadn't rendered in the docs when this was built, so the query wraps the
result in `TO_JSON_STRING` and the checker accepts either an array of strings or a struct of the groups in order
(unmatched optional groups as `NULL` or empty). The cloud notice says which shape BigQuery returned.

## Cost
One query over inlined literals: no table is scanned, well under the 20 MiB cap; effectively free.

## Result
| | Lines |
|---|---|
| packed items | 12 (1 without a pack size: `BREAD WHOLE WHEAT 1 @ 3.99`) |
| weighed items | 4 |
| left for a person | 2 (`COUPON OAT MILK -1.00`, `BAG FEE 0.10`) |

Parsed totals: r1001 $24.96, r1002 $44.47, r1003 $28.54. Full numbers in [`result.json`](result.json).
