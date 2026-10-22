# Day 17 · Databricks · Atomic goods receipts with `BEGIN ATOMIC`

**New platform feature:** **multi-statement transactions** (`BEGIN ATOMIC ... END;`) on Unity Catalog managed
tables, **generally available 2026-10-14**:
[release note](https://docs.databricks.com/aws/en/release-notes/product/2026/october) ("Transactions support both
managed Delta Lake and Apache Iceberg tables"). References:
[ATOMIC compound statement](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-txn-begin-atomic),
[transaction modes](https://docs.databricks.com/aws/en/transactions/transaction-modes),
[catalog commits](https://docs.databricks.com/aws/en/tables/features/catalog-commits). Every table a transaction
writes needs catalog commits (`'delta.feature.catalogManaged' = 'supported'`). Non-interactive `BEGIN ATOMIC` runs
on SQL warehouses and serverless compute and through the Statement Execution API, so it fits **Databricks Free
Edition**.

## What it does
The receiving dock books supplier deliveries into two tables: `receipts` (one row per delivery line) and `stock`
(on hand per SKU). Each delivery is **one** `BEGIN ATOMIC` block:

```sql
BEGIN ATOMIC
  IF (SELECT count(*) FROM receipts WHERE delivery_id = 'D2') > 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'rejected D2: already booked';
  END IF;
  INSERT INTO receipts VALUES ('D2', 1, '4011', 40), ('D2', 2, '3283', 30), ('D2', 3, '4225', 0);
  IF (SELECT count(*) FROM receipts WHERE delivery_id = 'D2'
        AND (qty <= 0 OR sku NOT IN (SELECT sku FROM stock))) > 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'rejected D2: bad line (qty <= 0 or unknown SKU)';
  END IF;
  MERGE INTO stock t USING (SELECT sku, sum(qty) qty FROM receipts WHERE delivery_id = 'D2' GROUP BY sku) r
    ON t.sku = r.sku WHEN MATCHED THEN UPDATE SET on_hand = t.on_hand + r.qty;
END;
```

The lines are written first and checked afterwards, so when a check fails the `SIGNAL` really has something to undo:
the whole block rolls back and neither table changes. Six deliveries arrive in a day:

| Delivery | Supplier | What's in it | Outcome |
|---|---|---|---|
| D1 | dairy co-op | milk 24, yogurt 36, butter 12 | committed |
| D2 | produce | bananas 40, apples 30, avocados **0** | **rolled back** (bad line) |
| D3 | dairy co-op | milk 24, eggs 30 | committed |
| D4 | frozen | ice cream 18, SKU **0999** (not stocked) | **rolled back** (bad line) |
| D1 again | dairy co-op | the D1 note resent | **refused** (already booked) |
| D6 | produce | corrected D2: bananas 40, apples 30, avocados 20 | committed |

## Why it matters
Without a transaction, a loader that inserts lines one by one and stops at the first bad one leaves a half-booked
delivery behind: here D2's bananas and apples and D4's ice cream (**88 units**) would sit in stock on hand for goods
the store refused, and the corrected D6 would add the bananas and apples a second time. With `BEGIN ATOMIC` the check, the receipt lines and
the stock update are one commit across two tables, with no staging table and no manual cleanup.

## How to run
```bash
python features/databricks/day-17-atomic-goods-receipts/receipts.py --local        # DuckDB; writes result.json
python features/databricks/day-17-atomic-goods-receipts/receipts.py --sql D2       # print one BEGIN ATOMIC block
python features/databricks/day-17-atomic-goods-receipts/receipts.py --databricks   # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day17_databricks_begin_atomic.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions: it creates a scratch schema with the two catalog-commit tables,
sends each delivery's `BEGIN ATOMIC` block through the Statement Execution API, reads back stock and receipts, and
drops everything. It fails if any delivery committed in one engine and rolled back in the other (or for a different
reason), or if any stock level or receipt line differs from the local run. Locally the same checks and `MERGE` run
in DuckDB between `BEGIN TRANSACTION` and `ROLLBACK` / `COMMIT`, and a plain-Python model checks both.

## Cost
About a dozen small statements on a serverless SQL warehouse (Free Edition, no charge). Two tiny tables are created
and dropped in the same run.

## Result
| SKU | Product | Before | After | Received |
|---|---|---|---|---|
| 0702 | whole milk 1gal | 18 | 66 | 48 |
| 0703 | greek yogurt 32oz | 12 | 48 | 36 |
| 0705 | butter 1lb | 20 | 32 | 12 |
| 0711 | large eggs 12ct | 24 | 54 | 30 |
| 0901 | vanilla ice cream 48oz | 10 | 10 | 0 |
| 3283 | honeycrisp apples (lb) | 45 | 75 | 30 |
| 4011 | bananas (lb) | 60 | 100 | 40 |
| 4225 | avocados (each) | 30 | 50 | 20 |

**3 of 6** deliveries booked (**8** receipt lines, **216** units); **3** rejected and rolled back whole, keeping
**88** units of half-booked deliveries out of stock, and the resent D1 note was not booked twice.

**Cloud run (2026-10-21, Free Edition):** in the post-merge workflow for #65, all six `BEGIN ATOMIC` blocks ran on a
serverless SQL warehouse through the Statement Execution API and matched the local DuckDB transactions exactly: D1,
D3 and D6 committed; D2 and D4 rolled back on the `SIGNAL` (their good lines never reached `receipts` or `stock`);
the resent D1 was refused; final stock and all 8 receipt lines identical.

Full numbers in [`result.json`](result.json).
