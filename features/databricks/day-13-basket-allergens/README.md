# Day 13 · Databricks · Basket and aisle allergens with collect_union

**New platform feature:** the **`collect_union`** aggregate function in
[Databricks SQL 2026.35](https://docs.databricks.com/aws/en/sql/release-notes/2026) (preview channel
**2026-08-24**): "The `collect_union` aggregate function returns the distinct union of the elements in arrays within
each group." (No function reference page is published yet, so this day relies on the release note and checks the
function against its long-hand equivalent in the same query.)

## What it does
18 products, each with an Open Food Facts-style array of allergen tags (`['gluten', 'milk', 'eggs', 'tree_nuts']`
for an almond croissant, an empty array for bananas), in 5 aisles, and 12 shopping baskets across two stores. One
read-only query over inline `VALUES` rolls the arrays up per basket and per aisle:

```sql
SELECT 'basket' AS level, l.basket_id AS group_key,
       collect_union(p.allergens) AS merged,                            -- new
       array_distinct(flatten(collect_list(p.allergens))) AS long_hand  -- what you had to write before
FROM lines l JOIN products p USING (sku)
GROUP BY l.basket_id
UNION ALL
SELECT 'aisle', aisle, collect_union(allergens), ... FROM products GROUP BY aisle
```
and returns each group's sorted allergen set, its size, whether it is nut-free (`arrays_overlap` with
`peanuts` / `tree_nuts`), and whether `collect_union` agrees with the long-hand version. Locally, Python set unions
compute the same thing (and the test cross-checks them with DuckDB's `list_distinct(flatten(list(...)))`); the cloud
result must match group by group.

## Why it matters
Allergen information lives on the product, but the questions are about groups: "does this basket contain nuts?"
(a school-lunch or click-and-collect check), "which aisles are nut-free?" (store layout, signage). Before, merging
arrays across rows meant `flatten(collect_list(...))` and then de-duplicating, which builds a list of every element
first. `collect_union` says what it means and merges as it goes.

## How to run
```bash
python features/databricks/day-13-basket-allergens/allergens.py --local        # Python sets; writes result.json
python features/databricks/day-13-basket-allergens/allergens.py --sql          # print the Databricks SQL
python features/databricks/day-13-basket-allergens/allergens.py --databricks   # needs DATABRICKS_HOST / _TOKEN
pytest -q tests/test_day13_databricks_collect_union.py
```
`cloud.sh` runs the Databricks mode in GitHub Actions on a serverless SQL warehouse (Statement Execution API) and
fails if any basket's or aisle's allergen set differs from the local one, or if `collect_union` disagrees with
`array_distinct(flatten(collect_list(...)))`.

## Cost
One small read-only query on a serverless SQL warehouse (trial credits; also fine on Free Edition). Nothing is
created.

## Result
**2 of 12** baskets are allergen-free (B01, B07: produce and frozen peas only); **5** contain nuts
(B02, B03, B08, B10, B11). Gluten is the most common allergen (8 baskets), then milk (6) and eggs (5). Basket B11
(shrimp, tuna, soy sauce, almond croissant) carries the most: 7 allergens.

| Aisle | Allergens (collect_union) | Nut-free |
|---|---|---|
| bakery | eggs, gluten, milk, sesame, tree_nuts | no |
| dairy | gluten, milk | yes |
| frozen | crustaceans, eggs, fish, gluten, milk | yes |
| pantry | fish, gluten, peanuts, sesame, soy, tree_nuts | no |
| produce | (none) | yes |

Products and baskets are made up. Full numbers in [`result.json`](result.json).
