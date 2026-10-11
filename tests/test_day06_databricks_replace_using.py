"""Day 6 Databricks: the local delete-then-insert run agrees with a plain-Python model of REPLACE USING (equality,
NULL never matches) and REPLACE ON with <=> (null-safe), and the SQL uses the new INSERT clauses."""
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-06-idempotent-reload"
spec = importlib.util.spec_from_file_location("reload_day06", FEATURE / "reload.py")
rl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl)


def python_replace(table, batch, null_safe):
    def match(t, s):
        if null_safe:
            return t["store"] == s["store"] and t["scan_date"] == s["scan_date"]  # None == None is True
        return None not in (t["scan_date"], s["scan_date"]) and t["store"] == s["store"] \
            and t["scan_date"] == s["scan_date"]
    return [t for t in table if not any(match(t, s) for s in batch)] + list(batch)


def snapshot(table):
    agg = defaultdict(lambda: [0, 0, 0])
    for r in table:
        a = agg[(r["store"], r["scan_date"])]
        a[0] += 1
        a[1] += r["units"]
        a[2] += r["amount_cents"]
    order = sorted(agg, key=lambda k: (k[0], k[1] is not None, k[1] or ""))
    return [{"store": s, "scan_date": d, "lines": agg[(s, d)][0], "units": agg[(s, d)][1],
             "amount_cents": agg[(s, d)][2]} for s, d in order]


def test_local_matches_python_model():
    base, corr = rl.load("sales_base"), rl.load("correction")
    local = rl.run_local(base, corr)
    using1 = python_replace(base, corr, null_safe=False)
    on1 = python_replace(base, corr, null_safe=True)
    assert local["base"] == snapshot(base)
    assert local["using_once"] == snapshot(using1)
    assert local["using_twice"] == snapshot(python_replace(using1, corr, null_safe=False))
    assert local["on_once"] == snapshot(on1)
    assert local["on_twice"] == snapshot(python_replace(on1, corr, null_safe=True))


def test_null_key_breaks_replace_using_idempotency_only():
    s = rl.summarise(rl.run_local(rl.load("sales_base"), rl.load("correction")))
    assert s["replace_on_idempotent"] and not s["replace_using_idempotent"]
    assert [s["totals"][k]["undated_rows"] for k in ("base", "using_once", "using_twice", "on_once", "on_twice")] \
        == [1, 2, 3, 1, 1]
    # the double-sent milk and eggs lines are gone and the bananas recount applied
    assert s["corrected_day"]["before"]["lines"] == 6 and s["corrected_day"]["after"]["lines"] == 4


def test_sql_uses_the_new_clauses():
    st = rl.statements(target="sales_using")
    assert list(st) == ["replace_using", "replace_on", "snapshot"]
    assert "REPLACE USING (store, scan_date)" in st["replace_using"]
    assert "REPLACE ON t.store <=> s.store AND t.scan_date <=> s.scan_date" in st["replace_on"]
    assert "--" not in "".join(st.values())  # comments stripped before sending
    ddl = rl.table_sql("correction", rl.load("correction"))
    assert "CREATE OR REPLACE TABLE microfeatures_day06.correction" in ddl and "NULL" in ddl


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    summary = rl.summarise(rl.run_local(rl.load("sales_base"), rl.load("correction")))
    assert {k: result[k] for k in summary} == summary
