"""Day 2 (Google Cloud): score two simple grocery "predictors" with BigQuery's new ML.METRICS function.

  python metrics.py --local                 # pure-Python metrics with ML.METRICS' documented definitions; writes result.json
  python metrics.py --compare KIND FILE    # compare BigQuery JSON output for one query with the local result
                                           # KIND: classification | classification_labels | regression

1. Classification: does the "organic" label predict a healthy product (health score >= 70)?
2. Regression: how well does the Nutri-Score letter alone (a=90, b=75, c=55, d=38, e=22) estimate the score?
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEALTHY = 70
GRADE_SCORE = {"a": 90, "b": 75, "c": 55, "d": 38, "e": 22}
CLS_KEYS = ["precision", "recall", "accuracy", "f1_score"]
REG_KEYS = ["mean_absolute_error", "mean_squared_error", "mean_squared_log_error", "median_absolute_error",
            "r2_score", "explained_variance"]
DIGITS = 4


def bigquery_sql(name: str, table: str) -> str:
    return (HERE / f"{name}.sql").read_text().replace("{table}", table)


def load_rows() -> list[dict]:
    with open(HERE / "data" / "products.csv", newline="") as f:
        return list(csv.DictReader(f))


def classification(pred: list[bool], actual: list[bool]) -> dict:
    tp = sum(p and a for p, a in zip(pred, actual))
    fp = sum(p and not a for p, a in zip(pred, actual))
    fn = sum(a and not p for p, a in zip(pred, actual))
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    return {"precision": precision, "recall": recall,
            "accuracy": sum(p == a for p, a in zip(pred, actual)) / len(actual),
            "f1_score": 2 * precision * recall / (precision + recall)}


def regression(pred: list[float], actual: list[float]) -> dict:
    n = len(actual)
    err = [a - p for p, a in zip(pred, actual)]
    mean_a = sum(actual) / n
    ss_tot = sum((a - mean_a) ** 2 for a in actual)
    return {
        "mean_absolute_error": sum(abs(e) for e in err) / n,
        "mean_squared_error": sum(e * e for e in err) / n,
        "mean_squared_log_error": sum((math.log1p(a) - math.log1p(p)) ** 2 for p, a in zip(pred, actual)) / n,
        "median_absolute_error": statistics.median(abs(e) for e in err),
        "r2_score": 1 - sum(e * e for e in err) / ss_tot,
        "explained_variance": 1 - statistics.pvariance(err) / statistics.pvariance(actual),
    }


def macro_classification(pred: list[str], actual: list[str]) -> dict:
    """ML.METRICS with STRING labels: per-label (one-vs-rest) metrics, then the unweighted mean."""
    per = [classification([p == lab for p in pred], [a == lab for a in actual]) for lab in sorted(set(actual) | set(pred))]
    return {k: sum(m[k] for m in per) / len(per) for k in CLS_KEYS}


def run_local_labels() -> dict:
    rows = load_rows()
    lab = lambda b: "healthy" if b else "other"  # noqa: E731
    return rounded(macro_classification([lab(r["organic"] == "true") for r in rows],
                                        [lab(int(r["health_score"]) >= HEALTHY) for r in rows]), CLS_KEYS)


def run_local() -> dict:
    rows = load_rows()
    cls = classification([r["organic"] == "true" for r in rows], [int(r["health_score"]) >= HEALTHY for r in rows])
    reg = regression([float(GRADE_SCORE[r["nutri_grade"]]) for r in rows], [float(r["health_score"]) for r in rows])
    return {"organic_predicts_healthy": rounded(cls, CLS_KEYS), "nutri_grade_predicts_score": rounded(reg, REG_KEYS)}


def rounded(d: dict, keys: list[str]) -> dict:
    return {k: round(float(d[k]), DIGITS) for k in keys}


def read_bq(path: str, keys: list[str]) -> dict:
    text = Path(path).read_text()
    start = text.find("[")  # bq may print a warning line before the JSON
    try:
        return rounded(json.loads(text[start:])[0], keys)
    except (ValueError, KeyError, IndexError, TypeError) as e:
        print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {text[:300]!r}")
        raise SystemExit(1)


def same(a: dict, b: dict) -> bool:
    return all(math.isclose(a[k], b[k], abs_tol=2 * 10 ** -DIGITS) for k in a)


if __name__ == "__main__":
    local = run_local()
    if len(sys.argv) > 3 and sys.argv[1] == "--compare":
        kind, path = sys.argv[2], sys.argv[3]
        expected, keys = {"classification": (local["organic_predicts_healthy"], CLS_KEYS),
                          "classification_labels": (run_local_labels(), CLS_KEYS),
                          "regression": (local["nutri_grade_predicts_score"], REG_KEYS)}[kind]
        cloud = read_bq(path, keys)
        print(kind, json.dumps(cloud))
        if not same(cloud, expected):
            print(f"::error title=BigQuery ML.METRICS ({kind}) differs from local::"
                  f"{json.dumps({'bigquery': cloud, 'local': expected})[:900]}")
            raise SystemExit(1)
        print(f"::notice title=Google Cloud day 2 ({kind})::ML.METRICS ran on BigQuery and matched local: {json.dumps(cloud)}")
    else:
        rows = load_rows()
        (HERE / "result.json").write_text(json.dumps({"engine": "python (local) / BigQuery ML.METRICS (cloud)",
                                                      "products": len(rows), **local,
                                                      "organic_predicts_healthy_macro_labels": run_local_labels()},
                                                     indent=2) + "\n")
        print(json.dumps(local, indent=2))
