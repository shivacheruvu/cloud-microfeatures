# Day 8 · Databricks · Cut-to-the-cent unit-price labels: `truncate` and `trim_array`

**New platform features:** the SQL functions **`truncate`** ("truncates a number to a specified scale without
rounding") and **`trim_array`** (removes a number of elements from the end of an array), both new in
[Databricks SQL 2026.38](https://docs.databricks.com/aws/en/sql/release-notes/2026): preview channel
**2026-09-21**, current channel 2026-09-28.

## What it does
Ten grocery items (pack price, promo price, pack size). Two read-only queries over inline `VALUES`:

1. **Shelf labels.** Unit price per 100 g / 100 ml is `price_cents / size` as an exact `DECIMAL`. The label shows
   `truncate(unit_price, 2)`, next to `round(unit_price, 2)` for comparison. The promo saving per 100 g is negative,
   and `truncate` goes **toward zero**, so the label never claims more saving than there is either.
2. **Void last N scans.** Each basket is the list of scanned barcodes plus how many the cashier voided at the end.
   `trim_array(scans, voids)` drops them, and `aggregate(...)` sums the receipt over what's left (including a basket
   where every scan was voided).

```sql
SELECT product, truncate(unit_price, 2) AS unit_label, round(unit_price, 2) AS unit_rounded,
       truncate(promo_saving, 2) AS saving_label FROM unit_prices;
SELECT basket, trim_array(scans, voids) AS kept,
       aggregate(trim_array(scans, voids), 0, (acc, b) -> acc + price_map.m[b]) AS total_cents FROM baskets ...;
```

## Why it matters
The label spec here says "cut to the cent, don't round": the shown unit price is never above the real one, and a
promo label never claims more saving than there is. Before `truncate` you wrote
`floor(x * 100) / 100` and got negative numbers wrong (`floor` of a -0.294 saving says -0.30). And "drop the last N
elements" used to be `slice(arr, 1, size(arr) - n)`; `trim_array(arr, n)` says what it means.

## How to run
```bash
python features/databricks/day-08-unit-price-labels/labels.py --local        # exact fractions; writes result.json
python features/databricks/day-08-unit-price-labels/labels.py --sql          # print the Databricks SQL
python features/databricks/day-08-unit-price-labels/labels.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN
pytest -q tests/test_day08_databricks_truncate_trim_array.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions on a serverless SQL warehouse (the Statement Execution API).
It fails if any truncated label, rounded value, saving, kept-scan list or basket total differs from the local
exact-fraction result. The test data stays clear of cent boundaries so intermediate decimal rounding can't matter.

## Cost
Two small queries on a serverless SQL warehouse (trial credits; also fine on Free Edition). Nothing is created.

## Result
| Item | Per | Unit price | Label (`truncate`) | Rounded | Promo saving label |
|---|---|---|---|---|---|
| rolled oats 1kg | 100 g | 0.449 | **0.44** | 0.45 | -0.05 |
| greek yogurt 32oz | 100 g | 0.6384 | **0.63** | 0.64 | -0.08 |
| peanut butter 16oz | 100 g | 0.7687 | **0.76** | 0.77 | |
| basmati rice 2kg | 100 g | 0.4495 | **0.44** | 0.45 | |
| olive oil 500ml | 100 ml | 2.198 | **2.19** | 2.20 | -0.40 |
| coffee beans 12oz | 100 g | 3.6735 | 3.67 | 3.67 | |
| almond milk 64oz | 100 ml | 0.2111 | 0.21 | 0.21 | |
| cheddar 8oz | 100 g | 1.9780 | **1.97** | 1.98 | -0.22 |
| spaghetti 16oz | 100 g | 0.3943 | 0.39 | 0.39 | |
| honey 12oz | 100 g | 2.35 | 2.35 | 2.35 | -0.29 (floor: -0.30) |

Rounding would overstate **6 of 10** unit prices; truncation never does.

| Basket | Scanned | Voided | Kept | Total |
|---|---|---|---|---|
| b1 | 4 | 1 | 3 | $13.77 |
| b2 | 3 | 0 | 3 | $31.47 |
| b3 | 5 | 2 | 3 | $8.07 |
| b4 | 2 | 2 | 0 | $0.00 |

Full numbers in [`result.json`](result.json).
