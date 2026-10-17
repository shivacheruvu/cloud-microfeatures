-- name: databricks
-- Which store's network is each checkout device actually on? The new IP address functions (Databricks SQL 2026.20):
-- try_ip_host cleans each reported address to its canonical form (NULL when it isn't an address), ip_version tells
-- IPv4 from IPv6, and ip_cidr_contains matches it to a store subnet. {subnets} / {devices} are inline VALUES lists
-- built from data/*.csv. Read-only: nothing is created.
WITH subnets AS (
  SELECT store_id, cidr FROM VALUES
{subnets}
  AS t(store_id, cidr)
), devices AS (
  SELECT device_id, home_store, try_ip_host(raw_ip) AS ip FROM VALUES
{devices}
  AS t(device_id, device_type, home_store, raw_ip)
), seen AS (
  SELECT d.device_id, d.home_store, d.ip, s.store_id AS seen_store
  FROM devices d
  LEFT JOIN subnets s
    ON CASE WHEN d.ip IS NULL OR ip_version(s.cidr) <> ip_version(d.ip) THEN false
            ELSE ip_cidr_contains(s.cidr, d.ip) END
)
SELECT device_id, ip, ip_version(ip) AS ip_version, seen_store,
       CASE WHEN ip IS NULL THEN 'bad_ip'
            WHEN seen_store IS NULL THEN 'unknown_network'
            WHEN seen_store = home_store THEN 'ok'
            ELSE 'wrong_store' END AS status
FROM seen
ORDER BY device_id
