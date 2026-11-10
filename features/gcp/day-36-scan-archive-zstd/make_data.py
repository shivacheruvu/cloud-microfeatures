"""Writes the day-36 sample scan exports (deterministic, synthetic: no real data). Run once; the CSVs are committed."""
import random
from pathlib import Path

PRODUCTS = [("0041303001189", "Whole milk 1 gal", "3.79"), ("0073296003224", "Crème fraîche 200 g", "4.49"),
            ("0011110889900", "Jalapeño peppers per lb", "1.29"), ("0070470496443", "Café molido 340 g", "7.99"),
            ("0024000163084", "Ñoquis de papa 500 g", "3.29"), ("4011", "Bananas per lb", "0.59"),
            ("0051500255162", "Peanut butter 16 oz", "3.19"), ("0072250011372", "Brötchen 6 ct", "2.99"),
            ("0012000161155", "Sparkling water 12 pk", "5.49"), ("0028400090865", "Tortilla chips 13 oz", "4.29"),
            ("0038000138416", "Corn flakes 18 oz", "4.59"), ("4065", "Green bell pepper", "0.99")]
STORES = [("chi-01", 140), ("den-02", 110), ("phx-03", 90), ("nyc-04", 160)]


def main() -> None:
    rnd = random.Random(36)
    out = Path(__file__).resolve().parent / "data"
    for store, n in STORES:
        lines = ["scanned_at,lane,barcode,product,qty,unit_price"]
        t = 7 * 3600
        for _ in range(n):
            t += rnd.randint(5, 240)
            code, name, price = rnd.choice(PRODUCTS)
            lines.append(f"2026-11-08T{t // 3600:02d}:{t // 60 % 60:02d}:{t % 60:02d},{rnd.randint(1, 6)},"
                         f"{code},{name},{rnd.choice([1, 1, 1, 2, 3])},{price}")
        (out / f"{store}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # a near-empty export: zstd's frame header costs more than it saves on a few bytes
    (out / "tiny-05.csv").write_text("scanned_at,lane,barcode,product,qty,unit_price\n", encoding="utf-8")


if __name__ == "__main__":
    main()
