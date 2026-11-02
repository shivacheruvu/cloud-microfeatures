"""Day 28 Google Cloud: the AI.TRANSLATE SQL, the number/product-word judge, and the cloud-output check (STRUCTs as
JSON strings, a warning line before the JSON, a changed price, and the failure paths)."""
import importlib.util
import json
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-28-bilingual-shelf-tags"
spec = importlib.util.spec_from_file_location("tags28", FEATURE / "shelf_tags.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)


def test_reference_and_result_json():
    s = st.summarise()
    assert s["reference_check"] == {"tags": 12, "numbers_kept": 12, "product_word": 12, "changed": 12}
    saved = json.loads((FEATURE / "result.json").read_text(encoding="utf-8"))
    assert {k: saved[k] for k in s} == s


def test_digit_groups_ignore_separator_but_not_value():
    assert st.digit_groups("$0.59/lb") == st.digit_groups("0,59 $/lb")
    assert st.digit_groups("2 for $5") != st.digit_groups("2 por $6")
    assert st.digit_groups("15.5 oz can $1.19") == ["1", "15", "19", "5"]


def test_sql_shape():
    q = st.sql()
    assert "AI.TRANSLATE(tag, 'es', source_language => 'en', return_details => TRUE)" in q
    assert q.count(" AS sku, ") == 12 and "ORDER BY sku" in q


def _out(tmp_path, results, prefix="", status="OK"):
    rows = [{"sku": t["sku"], "tag": t["tag"],
             "es": None if results.get(t["sku"]) is None else json.dumps({"result": results[t["sku"]], "status": status})}
            for t in st.tags() if t["sku"] in results]
    p = tmp_path / "out.json"
    p.write_text(prefix + json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _ref():
    return {t["sku"]: t["es_reference"] for t in st.tags()}


def test_compare_ok_with_scopes_warning(tmp_path, capsys):
    assert st.compare(_out(tmp_path, _ref(), prefix="WARNING: --scopes ignored\n")) == 0
    out = capsys.readouterr().out
    assert "::notice title=Google Cloud day 28::" in out and "numbers kept on 12 of 12" in out and "::warning" not in out


def test_changed_price_is_a_warning(tmp_path, capsys):
    r = _ref()
    r["P11"] = "Maíz dulce congelado 16 oz 2 por $6"
    r["P03"] = "Ripe bananas $0.59/lb"  # untranslated
    assert st.compare(_out(tmp_path, r)) == 0
    out = capsys.readouterr().out
    assert "numbers kept on 11 of 12" in out and "::warning title=Google Cloud day 28 tags to review::" in out
    assert "P11" in out and "P03" in out


def test_failures(tmp_path, capsys):
    r = _ref()
    del r["P01"]
    assert st.compare(_out(tmp_path, r)) == 1
    assert st.compare(_out(tmp_path, _ref(), status="403 Cloud Translation API has not been used")) == 1
    r = _ref()
    r["P02"] = None
    assert st.compare(_out(tmp_path, r)) == 1
    out = capsys.readouterr().out
    assert out.count("::error title=Google Cloud day 28 output check failed::") == 3
