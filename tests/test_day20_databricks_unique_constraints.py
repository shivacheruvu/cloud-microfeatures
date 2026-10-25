"""Day 20 Databricks: the DuckDB audit matches the plain-Python model, the DDL declares UNIQUE on the barcode and a
FOREIGN KEY that references it, an enforcing engine would refuse the relists, and the cloud checks accept a matching
run and reject a wrong one (no cloud)."""
import copy
import importlib.util
from decimal import Decimal
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-20-unique-barcodes"
spec = importlib.util.spec_from_file_location("barcodes_day20", FEATURE / "barcodes.py")
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)

LOCAL = bc.run_local()


def test_local_matches_pure_python():
    assert LOCAL == bc.pure_python()
    assert bc.compare(LOCAL, bc.pure_python()) == []


def test_audit_numbers():
    assert LOCAL["duplicate_upcs"] == [("041303001127", 2, 3, 8), ("072250011372", 2, 7, 10)]
    assert LOCAL["orphan_scans"] == [(110, "085239311004")]
    assert LOCAL["join_fanout"] == [(10, 13, Decimal("59.72"), Decimal("87.56"))]
    s = bc.summarise(LOCAL, bc.enforced_engine_rejects())
    assert s["overstated_by"] == "27.84" and s["products_without_barcode"] == 2


def test_nulls_do_not_clash_in_an_enforcing_engine():
    # bananas and apples both have a NULL barcode; only the two relists are refused
    assert bc.enforced_engine_rejects() == [8, 10]


def test_ddl_declares_unique_and_fk_to_it():
    b = bc.sql_blocks()
    assert "CONSTRAINT products_upc_uq UNIQUE (upc)" in b["create_products"]
    assert "FOREIGN KEY (upc) REFERENCES microfeatures_day20.products (upc)" in b["create_scans"]
    assert "{" not in b["create_scans"] and "NOT NULL" not in b["create_products"].split("upc", 1)[1].split(",")[0]


def test_api_rows_normalise_like_duckdb():
    api = {"duplicate_upcs": [["041303001127", "2", "3", "8"], ["072250011372", "2", "7", "10"]],
           "orphan_scans": [["110", "085239311004"]], "join_fanout": [["10", "13", "59.72", "87.56"]]}
    cloud = {k: bc.normalise(k, v) for k, v in api.items()}
    assert bc.compare(cloud, LOCAL) == []
    wrong = copy.deepcopy(cloud)
    wrong["join_fanout"] = [(10, 10, Decimal("59.72"), Decimal("59.72"))]   # duplicates silently rejected
    assert len(bc.compare(wrong, LOCAL)) == 1


def test_catalog_check():
    good = {"declared": [["products", "products_pk", "PRIMARY KEY"], ["products", "products_upc_uq", "UNIQUE"],
                         ["scans", "scans_pk", "PRIMARY KEY"], ["scans", "scans_upc_fk", "FOREIGN KEY"]],
            "fk_targets": [["scans_upc_fk", "products_upc_uq"]]}
    assert bc.check_catalog(good) == []
    bad = {"declared": good["declared"][:1] + good["declared"][2:], "fk_targets": [["scans_upc_fk", "products_pk"]]}
    assert len(bc.check_catalog(bad)) == 2
