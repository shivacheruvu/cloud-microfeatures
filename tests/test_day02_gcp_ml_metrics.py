"""Day 2 Google Cloud: local metrics follow ML.METRICS' documented definitions, checked by hand and with DuckDB."""
import importlib.util
import json
from pathlib import Path

import duckdb

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-02-ml-metrics"
spec = importlib.util.spec_from_file_location("metrics", FEATURE / "metrics.py")
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)
CSV = FEATURE / "data/products.csv"


def test_classification_from_confusion_matrix():
    # organic products: 6 healthy (TP), 3 not (FP); 5 healthy non-organic products missed (FN); 25 products
    con = duckdb.connect()
    tp, fp, fn, tn = con.execute(f"""SELECT
        COUNT(*) FILTER (WHERE organic AND health_score >= 70), COUNT(*) FILTER (WHERE organic AND health_score < 70),
        COUNT(*) FILTER (WHERE NOT organic AND health_score >= 70), COUNT(*) FILTER (WHERE NOT organic AND health_score < 70)
        FROM read_csv_auto('{CSV}')""").fetchone()
    p, r = tp / (tp + fp), tp / (tp + fn)
    expected = {"precision": p, "recall": r, "accuracy": (tp + tn) / (tp + fp + fn + tn), "f1_score": 2 * p * r / (p + r)}
    assert mm.run_local()["organic_predicts_healthy"] == mm.rounded(expected, mm.CLS_KEYS)


def test_regression_matches_duckdb():
    con = duckdb.connect()
    mae, mse, msle, med, r2 = con.execute(f"""
        WITH t AS (SELECT CAST(health_score AS DOUBLE) AS a,
                          CAST(CASE nutri_grade WHEN 'a' THEN 90 WHEN 'b' THEN 75 WHEN 'c' THEN 55 WHEN 'd' THEN 38
                               ELSE 22 END AS DOUBLE) AS p FROM read_csv_auto('{CSV}'))
        SELECT AVG(ABS(a - p)), AVG((a - p) ^ 2), AVG((LN(1 + a) - LN(1 + p)) ^ 2), MEDIAN(ABS(a - p)),
               1 - SUM((a - p) ^ 2) / SUM((a - (SELECT AVG(a) FROM t)) ^ 2) FROM t""").fetchone()
    local = mm.run_local()["nutri_grade_predicts_score"]
    expected = {"mean_absolute_error": mae, "mean_squared_error": mse, "mean_squared_log_error": msle,
                "median_absolute_error": med, "r2_score": r2}
    assert {k: local[k] for k in expected} == mm.rounded(expected, list(expected))


def test_odd_row_count_keeps_median_unambiguous():
    assert len(mm.load_rows()) % 2 == 1


def test_sql_uses_ml_metrics():
    for name, task in (("classification", "classification"), ("regression", "regression")):
        sql = mm.bigquery_sql(name, "p.d.t")
        assert "FROM ML.METRICS(" in sql and f"task_type => '{task}'" in sql and "`p.d.t`" in sql


def test_compare_reads_bq_json_with_warning(tmp_path):
    local = mm.run_local()
    f = tmp_path / "c.json"
    f.write_text("WARNING: --scopes ...\n" + json.dumps([{k: str(v) for k, v in local["organic_predicts_healthy"].items()}]))
    assert mm.same(mm.read_bq(str(f), mm.CLS_KEYS), local["organic_predicts_healthy"])


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    assert {k: result[k] for k in ("organic_predicts_healthy", "nutri_grade_predicts_score")} == mm.run_local()
