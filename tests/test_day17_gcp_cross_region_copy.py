"""Day 17 Google Cloud: the price book and its markdown are deterministic, the SQL builds / edits / fingerprints the
table, and the cloud check reads bq JSON (strings, a warning line first) and accepts a correct copy while rejecting a
copy in the wrong region, a copy that lost rows, a fingerprint mismatch, and a copy that followed the US edit."""
import copy
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-17-cross-region-price-book"
spec = importlib.util.spec_from_file_location("price_book_day17", FEATURE / "price_book.py")
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


def check_rows(rows, fps):
    agg = pb.aggregates(rows)
    return {k: (n, total, fps[k]) for k, (n, total) in agg.items()}


BEFORE_FP = {k: str(1000 + i) for i, k in enumerate(pb.aggregates(pb.price_book()))}
AFTER_FP = {**BEFORE_FP, "frozen": "-77", "ALL": "-78"}   # S05 is frozen
GOOD = {"us_at_copy": check_rows(pb.price_book(), BEFORE_FP), "eu": check_rows(pb.price_book(), BEFORE_FP),
        "us_after": check_rows(pb.edited_book(), AFTER_FP), "eu_after": check_rows(pb.price_book(), BEFORE_FP)}
META = {"location": "EU", "clustering": ["category"], "description": pb.DESCRIPTION, "num_rows": 36}


def test_price_book():
    book = pb.price_book()
    assert book == pb.price_book() and len(book) == 36 and len({r["sku"] for r in book}) == 36
    assert {r["category"] for r in book} == {c for c, _ in pb.CATEGORIES}
    agg, after = pb.aggregates(book), pb.aggregates(pb.edited_book())
    assert agg["ALL"][0] == sum(v[0] for k, v in agg.items() if k != "ALL") == 36
    assert agg["ALL"][1] - after["ALL"][1] == 30 and agg["frozen"][1] - after["frozen"][1] == 30


def test_sql():
    setup = pb.bigquery_sql("setup", "p.ds")
    assert setup.startswith("CREATE TABLE `p.ds.price_book`\nCLUSTER BY category")
    assert setup.count("STRUCT(") == 36 and "--" not in setup
    assert pb.bigquery_sql("edit", "p.ds").endswith("WHERE sku = 'S05'")
    check = pb.bigquery_sql("check", "p.ds.t")
    assert check.count("BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(t)))") == 2 and "`p.ds.t`" in check


def write_outputs(tmp_path, parts, meta):
    for name, rows in parts.items():
        out = [{"category": k, "skus": str(n), "total_cents": str(t), "fp": fp} for k, (n, t, fp) in rows.items()]
        (tmp_path / f"{name}.json").write_text("WARNING: --scopes ignored\n" + json.dumps(out))
    (tmp_path / "eu_meta.json").write_text(json.dumps({
        "tableReference": {"tableId": "price_book"}, "location": meta["location"],
        "clustering": {"fields": meta["clustering"]}, "description": meta["description"],
        "numRows": str(meta["num_rows"])}))


def test_reads_bq_output_and_accepts_good_copy(tmp_path):
    write_outputs(tmp_path, GOOD, META)
    parts = {n: pb.read_check(tmp_path / f"{n}.json") for n in GOOD}
    meta = pb.read_meta(tmp_path / "eu_meta.json")
    assert meta == META and parts == GOOD
    assert pb.compare(**parts, meta=meta) == []


def test_rejects_bad_copies():
    assert pb.compare(**GOOD, meta={**META, "location": "US"})
    assert pb.compare(**GOOD, meta={**META, "num_rows": 35})
    bad = copy.deepcopy(GOOD)
    bad["eu"]["dairy"] = (bad["eu"]["dairy"][0], bad["eu"]["dairy"][1], "999")       # fingerprint differs
    assert pb.compare(**bad, meta=META)
    followed = copy.deepcopy(GOOD)
    followed["eu_after"] = GOOD["us_after"]                                          # copy behaved like replication
    assert len(pb.compare(**followed, meta=META)) >= 2
    stale = copy.deepcopy(GOOD)
    stale["us_after"] = GOOD["us_at_copy"]                                           # markdown never applied
    assert pb.compare(**stale, meta=META)
