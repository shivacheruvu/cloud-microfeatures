"""Day 49 Google Cloud: DuckDB replay of the row access policy predicates, the SQL, and the cloud-output check
(stored predicates with backticks/brackets, a lagging view, a leaked row, an unreplayable predicate)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-49-price-policy-audit"
spec = importlib.util.spec_from_file_location("audit49", FEATURE / "audit.py")
au = importlib.util.module_from_spec(spec)
spec.loader.exec_module(au)


def test_replay_and_result_json():
    e = au.expected()
    assert [len(e["per_policy"][n]) for n in ("north_stores", "south_promos", "closed_store_s9")] == [6, 2, 0]
    assert e["per_policy"]["south_promos"] == [("S3", "MILK-2L"), ("S4", "EGGS-12")]
    assert len(e["visible"]) == 8 and len(e["uncovered"]) == 4
    assert all(s in ("S3", "S4") for s, _ in e["uncovered"])
    saved = json.loads((FEATURE / "result.json").read_text())
    s = au.summarise()
    assert {k: saved[k] for k in s} == s and s["dead_policies"] == ["closed_store_s9"]


def test_normalise():
    assert au.normalise("(`region` = 'north')") == "region = 'north'"
    assert au.normalise("((region = 'south') AND (on_promo))") == "(region = 'south') AND (on_promo)"
    assert au.replay("((`region` = 'south') AND (`on_promo`))") == au.expected()["per_policy"]["south_promos"]


def test_sql():
    assert "CREATE OR REPLACE TABLE `ds`.store_prices" in au.sql("setup", "ds") and au.sql("setup", "ds").count("STRUCT(") == 12
    pol = au.sql("policies", "ds")
    assert pol.count("CREATE OR REPLACE ROW ACCESS POLICY") == 3 and pol.count("serviceAccount:__RUNNER__") == 3
    assert "`ds`.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES" in au.sql("view", "ds")
    assert "`region-us`.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES" in au.sql("view-region", "ds")
    for part in ("setup", "policies", "view", "rows"):
        assert au.sql(part, "ds").startswith("--")


def _files(tmp_path, policies=None, rows=None, prefix="WARNING: --scopes\n"):
    if policies is None:
        policies = [{"table_catalog": "p", "table_schema": "ds", "table_name": "store_prices",
                     "row_access_policy_name": n, "filter_predicate": f"({p})", "grantees": ["x"]}
                    for n, p in au.POLICIES.items()]
    if rows is None:
        rows = [{"store_id": s, "sku": k} for s, k in au.expected()["visible"]]
    a, b = tmp_path / "pol.json", tmp_path / "rows.json"
    a.write_text(prefix + json.dumps(policies))
    b.write_text(json.dumps(rows))
    return str(a), str(b)


def test_compare_pass(tmp_path, capsys):
    assert au.compare(*_files(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "3/3 stored exactly as written" in out and "exactly the 8 predicted rows" in out and "::error" not in out


def test_compare_failures(tmp_path, capsys):
    pols = [{"row_access_policy_name": n, "filter_predicate": p} for n, p in au.POLICIES.items()]
    assert au.compare(*_files(tmp_path, policies=pols[:2])) == 1                      # view lagging
    bad = [dict(p) for p in pols]
    bad[1]["filter_predicate"] = "region = 'south'"
    assert au.compare(*_files(tmp_path, policies=bad)) == 1                           # predicate drifted
    bad[1]["filter_predicate"] = "SAFE.REGEXP_CONTAINS(region, r'^s')"
    assert au.compare(*_files(tmp_path, policies=bad)) == 1                           # not replayable
    rows = [{"store_id": s, "sku": k} for s, k in au.expected()["visible"]] + [{"store_id": "S4", "sku": "MILK-2L"}]
    assert au.compare(*_files(tmp_path, rows=rows)) == 1                              # leaked row
    assert au.compare(*_files(tmp_path, policies=[{"x": 1}])) == 1                    # unknown columns
    out = capsys.readouterr().out
    assert out.count("::error") == 5 and "uncovered rows leaked" in out
    p = tmp_path / "x.json"
    p.write_text("not json [")
    assert au.compare(str(p), str(p)) == 1


def test_redact_hides_account():
    assert "<account>" in au._redact("denied for runner@proj.iam.gserviceaccount.com")
