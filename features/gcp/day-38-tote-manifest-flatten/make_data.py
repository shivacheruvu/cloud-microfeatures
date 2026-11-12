"""Regenerate data/orders.jsonl: eight synthetic online grocery orders as the picker app exports them (fixed seed).

The `totes` value nests however the picker packed the order, which is the point of the day:
  tote -> bag -> item       the usual case
  tote -> item              an item dropped straight into a tote (no bag)
  tote -> bag -> bag -> item  a produce bag inside a bag
  item                      an express order: `totes` is a flat list of items
Items may carry a `subs` array (approved substitutes); those are objects, so JSON_FLATTEN leaves them inside the item.
One order was cancelled before picking and has no `totes`.
"""
import json
import random
from pathlib import Path

CATALOG = [  # sku, name, department, price in cents
    ("MLK-2L", "Whole milk 2 L", "dairy", 389), ("YOG-500", "Greek yogurt 500 g", "dairy", 449),
    ("EGG-12", "Free-range eggs x12", "dairy", 529), ("BAN-1KG", "Bananas 1 kg", "produce", 169),
    ("APL-6", "Honeycrisp apples x6", "produce", 599), ("SPN-200", "Baby spinach 200 g", "produce", 349),
    ("TOM-500", "Vine tomatoes 500 g", "produce", 429), ("BRD-SD", "Sourdough loaf", "bakery", 499),
    ("CRS-4", "Croissants x4", "bakery", 459), ("PAS-500", "Penne 500 g", "pantry", 199),
    ("RIC-2KG", "Basmati rice 2 kg", "pantry", 899), ("OIL-1L", "Olive oil 1 L", "pantry", 1149),
    ("CHK-1KG", "Chicken thighs 1 kg", "meat", 1099), ("SAL-300", "Salmon fillet 300 g", "meat", 1249),
    ("ICE-1L", "Vanilla ice cream 1 L", "frozen", 649), ("PEA-1KG", "Frozen peas 1 kg", "frozen", 299),
]


def item(rng, with_subs=False):
    sku, name, dept, cents = rng.choice(CATALOG)
    it = {"sku": sku, "name": name, "dept": dept, "qty": rng.randint(1, 3), "price_cents": cents}
    if with_subs:
        alt = rng.choice([c for c in CATALOG if c[2] == dept and c[0] != sku])
        it["subs"] = [{"sku": alt[0], "price_cents": alt[3]}]
    return it


def main():
    rng = random.Random(38)
    orders = []
    # 1-3: the usual tote -> bag -> item
    for n in range(1, 4):
        totes = [[[item(rng, with_subs=(n == 2 and b == 0)) for _ in range(rng.randint(1, 3))]
                  for b in range(rng.randint(1, 2))] for _ in range(rng.randint(1, 2))]
        orders.append({"order_id": f"W-{1000 + n}", "store": "chi-01", "totes": totes})
    # 4: one item dropped straight into a tote next to a bag
    orders.append({"order_id": "W-1004", "store": "chi-01",
                   "totes": [[[item(rng), item(rng)], item(rng)], [[item(rng, with_subs=True)]]]})
    # 5: a produce bag inside a bag (depth 4)
    orders.append({"order_id": "W-1005", "store": "den-02",
                   "totes": [[[item(rng), [item(rng), item(rng)]], [item(rng)]]]})
    # 6: express order, `totes` is a flat list of items
    orders.append({"order_id": "W-1006", "store": "den-02", "totes": [item(rng), item(rng), item(rng)]})
    # 7: mixed: a bare item in a tote, a nested bag, and a normal bag
    orders.append({"order_id": "W-1007", "store": "nyc-04",
                   "totes": [[item(rng), [[item(rng)], item(rng)]], [[item(rng), item(rng, with_subs=True)]]]})
    # 8: cancelled before picking, no totes
    orders.append({"order_id": "W-1008", "store": "nyc-04", "status": "cancelled"})
    out = Path(__file__).resolve().parent / "data" / "orders.jsonl"
    out.write_text("".join(json.dumps(o, separators=(",", ":")) + "\n" for o in orders))
    print(f"wrote {len(orders)} orders to {out}")


if __name__ == "__main__":
    main()
