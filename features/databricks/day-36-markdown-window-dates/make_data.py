"""Writes the day-36 sample data (deterministic, synthetic): daily unit sales for two stores over the 14 days before
2026-11-09 and the short-dated lots on hand that morning. Run once; the CSVs are committed."""
import random
from datetime import date, timedelta
from pathlib import Path

SKUS = {"milk-1gal": 30, "yogurt-4pk": 12, "bagels-6ct": 9, "salad-kit": 7, "rotisserie-chkn": 14, "hummus-10oz": 5}
STORES = {"chi-01": 1.0, "den-02": 0.6}


def main() -> None:
    rnd = random.Random(3636)
    out = Path(__file__).resolve().parent / "data"
    sales = ["store,sku,sale_date,units"]
    for d in range(14, 0, -1):
        day = date(2026, 11, 9) - timedelta(days=d)
        weekend = 1.3 if day.isoweekday() >= 6 else 1.0
        for store, scale in STORES.items():
            for sku, base in SKUS.items():
                sales.append(f"{store},{sku},{day},{max(0, round(base * scale * weekend * rnd.uniform(0.7, 1.3)))}")
    (out / "sales.csv").write_text("\n".join(sales) + "\n")
    lots = ["store,sku,lot,sell_by,on_hand"]
    n = 0
    for store in STORES:
        for sku in SKUS:
            for _ in range(2):
                n += 1
                lots.append(f"{store},{sku},L{n:03d},{date(2026, 11, 8) + timedelta(days=rnd.randint(0, 9))},"
                            f"{rnd.randint(4, 60)}")
    (out / "lots.csv").write_text("\n".join(lots) + "\n")


if __name__ == "__main__":
    main()
