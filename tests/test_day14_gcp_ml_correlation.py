"""Day 14 Google Cloud: the data file is reproducible and tie-free, the pure-Python Pearson and Spearman agree with
numpy, each store's planted driver comes out on top, the BigQuery SQL calls ML.CORRELATION twice with the data
inlined, and the comparison accepts bq-shaped output that matches and rejects any difference (no cloud)."""
import importlib.util
import json
from pathlib import Path

import numpy as np

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-14-ice-cream-drivers"
spec = importlib.util.spec_from_file_location("drivers_day14", FEATURE / "drivers.py")
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)

DAYS = dr.load()
LOCAL = dr.run_local(DAYS)


def bq_rows(local):  # bq --format=json returns every value as a string
    return [{"method": m, "store": s, "corr_col": c, "correlation": repr(v["correlation"]),
             "segment_size": str(v["segment_size"])} for (m, s, c), v in local.items()]


def test_data_is_reproducible_and_tie_free():
    assert dr.make_data() == DAYS and len(DAYS) == 84
    for col in ("ice_cream_revenue",) + dr.COLS:
        values = [r[col] for r in DAYS]
        assert len(set(values)) == len(values), col


def test_local_matches_numpy():
    def rank(x):
        return np.argsort(np.argsort(x)).astype(float)
    for (method, seg, col), v in LOCAL.items():
        rows = DAYS if seg == "ALL" else [r for r in DAYS if r["store"] == seg]
        x = np.array([float(r[col]) for r in rows])
        y = np.array([float(r["ice_cream_revenue"]) for r in rows])
        if method == "SPEARMAN":
            x, y = rank(x), rank(y)
        assert abs(np.corrcoef(x, y)[0, 1] - v["correlation"]) < 1e-12
        assert v["segment_size"] == (84 if seg == "ALL" else 28)


def test_planted_drivers():
    s = dr.summarise(LOCAL)
    assert s["top_driver"] == {"ALL": "max_temp_c", "S1": "max_temp_c", "S2": "promo_pct", "S3": "foot_traffic"}
    p = s["correlation"]["PEARSON"]
    assert p["S1"]["max_temp_c"] > 0.9 and p["S2"]["promo_pct"] > 0.9 and p["S3"]["foot_traffic"] > 0.9
    assert p["ALL"]["max_temp_c"] < 0.6                       # pooling the stores hides the weather effect
    assert s["correlation"]["SPEARMAN"]["S2"]["promo_pct"] < p["S2"]["promo_pct"] - 0.1   # stepwise promo levels


def test_compare_accepts_match_and_rejects_differences():
    rows = bq_rows(LOCAL)
    assert len(rows) == 24 and dr.compare(rows, LOCAL) == []
    assert dr.compare(rows[1:], LOCAL)                                       # missing row
    assert dr.compare(rows + rows[:1], LOCAL)                                # duplicate row
    off = json.loads(json.dumps(rows))
    off[0]["correlation"] = repr(float(off[0]["correlation"]) + 1e-4)       # correlation off
    assert dr.compare(off, LOCAL)
    off = json.loads(json.dumps(rows))
    off[0]["segment_size"] = "27"                                            # wrong segment size
    assert dr.compare(off, LOCAL)
    off = json.loads(json.dumps(rows))
    off[0]["correlation"] = None                                             # NULL correlation
    assert dr.compare(off, LOCAL)


def test_sql_calls_ml_correlation_with_inlined_data():
    sql = dr.bigquery_sql(DAYS)
    assert sql.count("FROM ML.CORRELATION(") == 2
    assert "method => 'PEARSON'" in sql and "method => 'SPEARMAN'" in sql
    assert "dimension_cols => ['store']" in sql and "target_col => 'ice_cream_revenue'" in sql
    assert "{days}" not in sql and sql.count("STRUCT(") == 84
