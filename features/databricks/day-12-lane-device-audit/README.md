# Day 12 · Databricks · Lane device network audit with the new IP address functions

**New platform feature:** **IP address functions** in [Databricks SQL 2026.20](https://docs.databricks.com/aws/en/sql/release-notes/2026)
(preview channel **2026-06-29**, current channel from 2026-07-13): "A set of SQL functions is now available for
working with IPv4 and IPv6 addresses and CIDR blocks", including `ip_host`, `ip_cidr`, `ip_version`,
`ip_cidr_contains` and `try_*` variants. This day uses three of them:
[`try_ip_host`](https://docs.databricks.com/aws/en/sql/language-manual/functions/try_ip_host),
[`ip_version`](https://docs.databricks.com/aws/en/sql/language-manual/functions/ip_version) and
[`ip_cidr_contains`](https://docs.databricks.com/aws/en/sql/language-manual/functions/ip_cidr_contains).

## What it does
Four stores, each with an IPv4 `/24` and an IPv6 `/64` for its lane devices, and 40 devices (registers,
self-checkouts, scales, handheld scanners) with the store they're registered to and the address they last reported.
Handhelds use IPv6, and some asset records write it long-hand in capitals. Six problems are planted. One read-only
query over inline `VALUES` audits them all:

```sql
devices AS (SELECT device_id, home_store, try_ip_host(raw_ip) AS ip FROM ...)   -- canonical form, NULL for junk
...
FROM devices d LEFT JOIN subnets s
  ON CASE WHEN d.ip IS NULL OR ip_version(s.cidr) <> ip_version(d.ip) THEN false
          ELSE ip_cidr_contains(s.cidr, d.ip) END
```
and labels each device `ok`, `wrong_store`, `unknown_network` or `bad_ip`. Locally, Python's standard `ipaddress`
module does the same audit, and the cloud result must match it device by device.

## Why it matters
Store devices drift: a scale gets lent to another store, a handheld roams onto the neighbour's Wi-Fi, a register
lands on the guest network, the asset register has typos. Before these functions, matching an address to a subnet
in SQL meant string splitting and bit arithmetic, and IPv6 was mostly out of reach. Now it's one predicate that
handles both versions, and `try_ip_host` both cleans `2001:0DB8:0012:...:00A2` to `2001:db8:12:1::a2` and turns junk
into NULL instead of failing the query.

## How to run
```bash
python features/databricks/day-12-lane-device-audit/audit.py --local        # Python ipaddress; writes result.json
python features/databricks/day-12-lane-device-audit/audit.py --sql          # print the Databricks SQL
python features/databricks/day-12-lane-device-audit/audit.py --databricks   # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day12_databricks_ip_functions.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions on a serverless SQL warehouse (Statement Execution API) and
fails if any device's canonical IP, IP version, matched store or status differs from the local audit.

## Cost
One small read-only query on a serverless SQL warehouse (trial credits; also fine on Free Edition). Nothing is
created.

## Result
**34 of 40** devices are on their own store's network (30 IPv4, 8 IPv6, 2 unreadable); 3 long-hand IPv6 addresses
are cleaned to canonical form. Flagged:

| Device | Registered to | Reported | Status | What happened |
|---|---|---|---|---|
| S1-SCL-2 (scale) | S1 Downtown | `10.12.4.62` | wrong_store (S4) | lent to Eastfield, still registered to Downtown |
| S2-HH-1 (handheld) | S2 Riverside | `2001:db8:12:3::b1` | wrong_store (S3) | roamed onto Southgate's network |
| S3-REG-4 (register) | S3 Southgate | `10.99.0.15` | unknown_network | on the guest Wi-Fi |
| S4-HH-2 (handheld) | S4 Eastfield | `2001:db8:99::7` | unknown_network | unknown IPv6 network |
| S2-SCO-2 (self-checkout) | S2 Riverside | `10.12.2.300` | bad_ip | typo in the asset register |
| S4-REG-3 (register) | S4 Eastfield | `lane-07` | bad_ip | hostname instead of an address |

All addresses are private (`10.0.0.0/8`) or documentation (`2001:db8::/32`) ranges. Full numbers in
[`result.json`](result.json).
