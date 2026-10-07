# Day 2 · Databricks · Lane counters with `counter_diff`

**New platform feature:** [`counter_diff` SQL window function](https://docs.databricks.com/aws/en/sql/language-manual/functions/counter_diff),
released **2026-09-14** ([release notes](https://docs.databricks.com/aws/en/release-notes/product/2026/september)),
Databricks SQL and Databricks Runtime 19+.

## What it does
Every checkout lane's scanner reports a **cumulative** item counter every 15 minutes. When a scanner reboots, its
counter restarts near zero. The query turns those readings into **items scanned per lane** for the store day,
counts the reboots, and finds each lane's busiest 15 minutes.

```sql
counter_diff(scan_counter) OVER (PARTITION BY lane ORDER BY read_at) AS items
```
`counter_diff` returns the increase since the previous reading, and `NULL` on the first reading and whenever the
counter goes down (a reset), so reboots are skipped instead of being counted as huge negative or positive jumps.

## Why it matters
Cumulative counters are everywhere in operations data (scanners, meters, request counters), and the usual shortcuts
are wrong as soon as a device restarts. Here, `MAX(counter) - MIN(counter)` reports **10,443** items for the day;
the real total from the deltas is **4,964**. Before `counter_diff` this took a `LAG` plus a hand-written reset check.

## How to run
```bash
python lane_counters.py --local        # DuckDB (counter_diff translated to LAG + reset check); writes result.json
python lane_counters.py --databricks   # needs DATABRICKS_HOST / DATABRICKS_TOKEN; runs on a serverless SQL warehouse
```
In CI, `cloud.sh` runs the query on Databricks through the SQL Statement Execution API and fails unless the
result is identical to the local one. The test (`tests/test_day02_databricks_lane_counters.py`) checks the query
against a plain-Python counter walk and checks the local translation against the documented `counter_diff` examples.

## Cost
One small statement on a serverless SQL warehouse (seconds of compute, plus idle time until auto-stop). Covered by
the 14-day trial credits. Also works on Databricks Free Edition (serverless SQL only).

## Result (5 lanes × 29 readings, 07:00–14:00)
| Lane | Items scanned | Resets | Peak per 15 min | Naive MAX − MIN |
|---|---|---|---|---|
| lane-1 | 1,006 | 0 | 67 | 1,006 |
| lane-2 | 946 | 1 | 60 | 2,186 |
| lane-3 | 976 | 0 | 61 | 976 |
| lane-4 | 807 | 2 | 55 | 1,922 |
| self-checkout | 1,229 | 1 | 79 | 4,353 |

Items scanned during the interval in which a scanner rebooted are unknown and not counted (that's what the
`NULL` means). The readings are synthetic (seeded), one store day.
