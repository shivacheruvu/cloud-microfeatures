"""Day 8 Google Cloud: the local run keys 10 products (2 new from the feed), every statement renders, and the cloud
comparison accepts sparse, loosely ordered identity keys but rejects repeats, off-grid keys, an accepted hand-written
key and wrong facts (no cloud)."""
import importlib.util
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-08-product-keys"
spec = importlib.util.spec_from_file_location("keys_day08", FEATURE / "keys.py")
kd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kd)

LOCAL = kd.run_local()
INFO = [{"column_name": "product_key", "is_identity": "YES", "identity_generation": "ALWAYS",
         "identity_start": "1000", "identity_increment": "10"}]


def cloud(keymap=lambda k: k):
    """BigQuery-shaped JSON rows (numbers as strings) with keys remapped to imitate sparse generation."""
    products = [{"product_key": str(keymap(p["product_key"])), "barcode": p["barcode"], "name": p["name"]}
                for p in LOCAL["products"]]
    facts = [{"product_key": str(keymap(f["product_key"])), "name": f["name"], "units": str(f["units"]),
              "scans": str(f["scans"])} for f in LOCAL["facts"]]
    return products, facts


def test_local_keys():
    s = kd.summarise(LOCAL)
    assert s["products"] == 10 and s["first_batch"] == 8
    assert s["new_in_feed"] == ["ginger kombucha 16oz", "sparkling water 12pk"]
    assert s["keys"] == list(range(1000, 1100, 10)) and s["key_errors"] == []
    facts = {f["name"]: (f["units"], f["scans"]) for f in LOCAL["facts"]}
    assert facts["ginger kombucha 16oz"] == (6, 2) and facts["whole milk 1gal"] == (4, 2)
    assert sum(u for u, _ in facts.values()) == 18


def test_statements_render():
    sql = kd.statements("proj.ds")
    assert set(sql) == {"create", "load", "add_new", "explicit_key", "products", "identity_info", "scan_facts"}
    assert "GENERATED ALWAYS AS IDENTITY (START WITH 1000 INCREMENT BY 10)" in sql["create"]
    assert all("{" not in s for s in sql.values())
    assert "product_key" not in sql["load"] and "product_key" not in sql["add_new"]


def test_compare_accepts_sparse_keys():
    products, facts = cloud(lambda k: 1000 + (k - 1000) * 37)   # gaps and any order are fine
    errors, warnings, summary = kd.compare(products, INFO, facts, True, LOCAL)
    assert errors == [] and warnings == [] and summary["dense"] is False
    errors, warnings, _ = kd.compare(products, [], facts, True, LOCAL)
    assert errors == [] and len(warnings) == 1


def test_compare_rejects():
    products, facts = cloud(lambda k: 1000 if k < 1030 else k)   # repeated keys
    assert any("repeated" in e for e in kd.compare(products, INFO, facts, True, LOCAL)[0])
    products, facts = cloud(lambda k: k + 5)                       # off the 10-step grid
    assert any("grid" in e for e in kd.compare(products, INFO, facts, True, LOCAL)[0])
    products, facts = cloud()
    assert any("hand-written" in e for e in kd.compare(products, INFO, facts, False, LOCAL)[0])
    facts[0] = {**facts[0], "units": "99"}
    assert any("scan facts" in e for e in kd.compare(products, INFO, facts, True, LOCAL)[0])
