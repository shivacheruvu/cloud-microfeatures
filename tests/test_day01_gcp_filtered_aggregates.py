"""Day 1 Google Cloud: the WHERE-in-aggregate scorecard (run locally on DuckDB) matches plain Python."""
import csv
import importlib.util
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-01-filtered-aggregates"
spec = importlib.util.spec_from_file_location("scorecard", FEATURE / "scorecard.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def sql_round(x):
    """SQL ROUND(x, 1) rounds halves away from zero; Python's round() does not."""
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def python_reference():
    with open(FEATURE / "data/products.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    avg = lambda xs: sql_round(sum(xs) / len(xs)) if xs else None  # noqa: E731
    out = []
    for cat in {r["category"] for r in rows}:
        rs = [r for r in rows if r["category"] == cat]
        score = lambda pred: [int(r["health_score"]) for r in rs if pred(r)]  # noqa: E731
        out.append({
            "category": cat, "products": len(rs),
            "grade_a_or_b": sum(r["nutri_grade"] in ("a", "b") for r in rs),
            "with_high_risk_additives": sum(int(r["high_risk_additives"]) > 0 for r in rs),
            "avg_score": avg(score(lambda r: True)),
            "avg_score_organic": avg(score(lambda r: r["organic"] == "true")),
            "avg_score_conventional": avg(score(lambda r: r["organic"] == "false")),
        })
    return sorted(out, key=lambda r: (-r["avg_score"], r["category"]))


def test_scorecard_matches_reference():
    assert sc.run_local() == python_reference()


def test_sql_uses_where_inside_aggregates():
    sql = sc.bigquery_sql("p.d.t")
    assert "AVG(health_score WHERE organic)" in sql and "COUNT(barcode WHERE nutri_grade IN ('a', 'b'))" in sql


def test_translation_handles_nested_parentheses():
    assert sc.to_duckdb("SELECT COUNT(x WHERE y IN (1, 2)) FROM t") == "SELECT COUNT(x) FILTER (WHERE y IN (1, 2)) FROM t"


def test_compare_accepts_bigquery_json_strings():
    as_bq = [{k: (None if v is None else str(v)) for k, v in r.items()} for r in sc.run_local()]
    assert sc.normalise(as_bq) == sc.run_local()


def test_result_json_is_current():
    assert json.loads((FEATURE / "result.json").read_text())["by_category"] == sc.run_local()
