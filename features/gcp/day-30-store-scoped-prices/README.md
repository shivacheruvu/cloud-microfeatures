# Day 30 · Google Cloud · One shelf-price view for every store, scoped with `SECURE_CONTEXT`

**New platform feature:** **`SECURE_CONTEXT` function (Preview)**, in the
[BigQuery release notes](https://docs.cloud.google.com/bigquery/docs/release-notes) on **2026-11-02**: "returns a
request-scoped secure context parameter supplied at query runtime, so logical views can filter data by context such
as user attributes or tenant IDs". The values travel in the new `secureContext.secureParameterEntries` field of the
[`jobs.query`](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query) request body (also on
`JobConfigurationQuery`). Reference:
[security functions](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/security_functions#secure_context).
(The docs pages didn't load in this unattended run; the request field and the rules below were taken from a public
GitHub mirror of the BigQuery docs and the generated API clients via code search.)

## What it does
A store app shouldn't need its own view, or its own SQL, per store and per role. [`store_view.py`](store_view.py)
builds a 24-row price table (3 stores × 8 SKUs, with unit cost) and **one** view:

```sql
CREATE OR REPLACE VIEW `<dataset>.my_store_prices` AS
SELECT sku, item, shelf_price, on_promo,
  IF(SECURE_CONTEXT('role') = 'buyer', unit_cost, NULL) AS unit_cost,
  IF(SECURE_CONTEXT('role') = 'buyer', ROUND((shelf_price - unit_cost) * 100 / shelf_price, 1), NULL) AS margin_pct
FROM `<dataset>.store_prices`
WHERE store_id = SECURE_CONTEXT('store_id')
```

Every caller runs the same `SELECT ... FROM my_store_prices ORDER BY sku`, with no store or role in the SQL, and
passes who it is in the request:

```json
{"query": "...", "secureContext": {"secureParameterEntries": {"store_id": "north", "role": "buyer"}}}
```

## Result (local reference; the cloud answers must equal it row for row)
| Caller (secure context) | Rows | Promos | Basket of 8 | Unit cost / margin | Avg margin |
|---|---|---|---|---|---|
| east clerk | 8 | 2 | $42.73 | hidden (NULL) | – |
| west clerk | 8 | 1 | $44.14 | hidden | – |
| north clerk | 8 | 0 | $43.42 | hidden | – |
| north buyer | 8 | 0 | $43.42 | **shown** | **36.2%** |
| `store_id = south` (no such store) | 0 | 0 | $0 | – | – |

The cloud run also checks the documented rules, reported as notices (or warnings if BigQuery doesn't enforce them):
a query with no `secureContext`, or one missing the `role` key, must fail; and `SELECT SECURE_CONTEXT('store_id')`
at the top level of an ad hoc query must fail (it works only inside logical views).

## Why it matters
Store apps, kiosk dashboards and supplier portals all read the same price data with different scopes. Before, that
meant one view per store (and per role), or building the filter into client SQL that anyone could edit. Now the scope
is a request attribute the calling service sets, and the view is the only place the rule lives. (It's a filter, not a
grant: whoever can call jobs.query on the view can set any context, so the context has to come from a trusted
backend, not from the end user.)

## How to run
```bash
python features/gcp/day-30-store-scoped-prices/store_view.py          # local reference, writes result.json
python features/gcp/day-30-store-scoped-prices/store_view.py --sql    # the DDL and the caller query
pytest -q tests/test_day30_gcp_secure_context.py                      # includes the view SQL on DuckDB
bash features/gcp/day-30-store-scoped-prices/cloud.sh                 # needs GCP auth (GitHub Actions)
```

## Cost
About ten tiny queries at BigQuery's 10 MiB minimum each (capped at 20 MiB): well under a cent. The scratch dataset
is dropped at the end; its tables expire after 2.4 hours anyway.

## Limits of this slice
Synthetic data; two context keys; one view. Extract jobs also accept a `secureContext` (per the API reference),
not covered here.
