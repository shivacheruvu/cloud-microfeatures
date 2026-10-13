"""Day 8 Databricks: local labels truncate toward zero and never overstate, voids drop the last N scans, and the
cloud comparison accepts Statement-Execution-shaped rows that match and rejects rounded labels (no cloud)."""
import importlib.util
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-08-unit-price-labels"
spec = importlib.util.spec_from_file_location("labels_day08", FEATURE / "labels.py")
lb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lb)

PRODS, BASK = lb.products(), lb.baskets()
LOCAL = lb.run_local(PRODS, BASK)


def test_trunc_toward_zero():
    assert lb.trunc2(Fraction(449, 1000)) == Decimal("0.44")
    assert lb.trunc2(Fraction(-100, 340)) == Decimal("-0.29")   # floor would say -0.30
    assert lb.trunc2(Fraction(235, 100)) == Decimal("2.35")
    assert lb.round2(Fraction(8990, 20000)) == Decimal("0.45")  # 0.4495 rounds up


def test_labels_never_overstate():
    for l in LOCAL["labels"]:
        assert l["unit_label"] <= l["unit_price"] < l["unit_label"] + Decimal("0.01")
        assert l["saving_label"] <= 0
    assert lb.summarise(LOCAL)["rounding_would_overstate"] == 6


def test_no_value_near_a_cent_boundary():
    # the cloud quotient is a DECIMAL with 11+ places; keep data well clear of x.xx9999.. so it truncates the same
    for p in PRODS:
        for x in (Fraction(p["price_cents"], p["size"]), Fraction(p["promo_cents"] - p["price_cents"], p["size"])):
            frac = abs(x) * 100 - int(abs(x) * 100)
            assert frac == 0 or frac < Fraction(9999, 10000)


def test_baskets():
    got = {b["basket"]: (b["kept"], b["total_cents"]) for b in LOCAL["baskets"]}
    assert got["b1"] == (["0001", "0002", "0003"], 449 + 579 + 349)
    assert got["b2"][1] == 1099 + 1249 + 799
    assert got["b3"] == (["0009", "0009", "0008"], 179 * 2 + 449)
    assert got["b4"] == ([], 0)


def cloud_rows(labels_override=None):
    lr = [[l["barcode"], l["product"], str(l["unit_price"]) + "00000", str(l["unit_label"]), str(l["unit_rounded"]),
           str(l["saving_label"])] for l in LOCAL["labels"]]
    if labels_override:
        lr = labels_override(lr)
    br = [[b["basket"], "[" + ",".join(f'"{x}"' for x in b["kept"]) + "]", str(b["items"]), str(b["total_cents"])]
          for b in LOCAL["baskets"]]
    return lb.parse_cloud(lr, br)


def test_compare_accepts_match_and_rejects_rounding():
    assert lb.compare(cloud_rows(), LOCAL) == []

    def rounded(rows):
        return [r[:3] + [r[4]] + r[4:] for r in rows]   # label column replaced by the rounded value
    errors = lb.compare(cloud_rows(rounded), LOCAL)
    assert len(errors) == 6 and "unit_label" in errors[0]


def test_sql_uses_the_new_functions():
    sql = lb.sql_statements(PRODS, BASK)
    assert "truncate(unit_price, 2)" in sql["labels"] and "truncate(promo_saving, 2)" in sql["labels"]
    assert "trim_array(scans, voids)" in sql["baskets"]
    assert "{products}" not in sql["labels"] + sql["baskets"]
