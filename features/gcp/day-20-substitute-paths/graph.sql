-- name: setup
-- Node and edge tables (keys declared NOT ENFORCED, as in the graph docs), then the property graph over them.
-- A graph copies no data: it is a logical view over the two tables.
CREATE OR REPLACE TABLE `{ds}`.products (
  sku STRING NOT NULL, name STRING, in_stock BOOL, price_cents INT64,
  PRIMARY KEY (sku) NOT ENFORCED
);
CREATE OR REPLACE TABLE `{ds}`.substitutes (
  from_sku STRING NOT NULL, to_sku STRING NOT NULL,
  PRIMARY KEY (from_sku, to_sku) NOT ENFORCED
);
INSERT INTO `{ds}`.products VALUES {product_rows};
INSERT INTO `{ds}`.substitutes VALUES {edge_rows};
CREATE OR REPLACE PROPERTY GRAPH `{ds}`.substitution_graph
  NODE TABLES (
    `{ds}`.products KEY (sku) LABEL Product
  )
  EDGE TABLES (
    `{ds}`.substitutes KEY (from_sku, to_sku)
      SOURCE KEY (from_sku) REFERENCES products (sku)
      DESTINATION KEY (to_sku) REFERENCES products (sku)
      LABEL SubstitutesFor
  );

-- name: walks
-- Every substitution walk of 1 to 3 hops that starts at an out-of-stock product. IS_ACYCLIC (GA 2026-08-31) flags
-- walks that come back to a product they already visited (A -> B -> A), PATH_LENGTH counts the hops.
GRAPH `{ds}`.substitution_graph
MATCH p = (src:Product)-[s:SubstitutesFor]->{1,3}(dst:Product)
WHERE NOT src.in_stock
RETURN src.sku AS src_sku, dst.sku AS dst_sku, dst.in_stock AS dst_in_stock,
       PATH_LENGTH(p) AS hops, IS_ACYCLIC(p) AS acyclic, IS_TRAIL(p) AS trail;

-- name: walks_fixed
-- Fallback with one fixed-length pattern per hop count, used only if the quantified pattern above is refused.
GRAPH `{ds}`.substitution_graph
MATCH p = (src:Product)-[e1:SubstitutesFor]->{fixed_tail}
WHERE NOT src.in_stock
RETURN src.sku AS src_sku, dst.sku AS dst_sku, dst.in_stock AS dst_in_stock,
       PATH_LENGTH(p) AS hops, IS_ACYCLIC(p) AS acyclic, IS_TRAIL(p) AS trail;

-- name: walks_duckdb
-- Local twin of `walks`: a recursive CTE that enumerates the same walks and checks for a repeated node.
WITH RECURSIVE w(src, dst, hops, nodes, edges) AS (
  SELECT e.from_sku, e.to_sku, 1, [e.from_sku, e.to_sku], [e.from_sku || '>' || e.to_sku]
  FROM substitutes e JOIN products p ON p.sku = e.from_sku
  WHERE NOT p.in_stock
  UNION ALL
  SELECT w.src, e.to_sku, w.hops + 1, list_append(w.nodes, e.to_sku), list_append(w.edges, e.from_sku || '>' || e.to_sku)
  FROM w JOIN substitutes e ON e.from_sku = w.dst
  WHERE w.hops < 3
)
SELECT w.src AS src_sku, w.dst AS dst_sku, p.in_stock AS dst_in_stock, w.hops,
       len(list_distinct(w.nodes)) = len(w.nodes) AS acyclic,
       len(list_distinct(w.edges)) = len(w.edges) AS trail
FROM w JOIN products p ON p.sku = w.dst;
