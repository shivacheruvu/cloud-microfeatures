"""Day 30 (Databricks): per-store basket-size bands from one pandas-API-on-Spark call,
`psdf.groupby("store")["basket"].describe()` (SeriesGroupBy.describe, new on serverless 2026-08-17).

  python basket_describe.py [OUT.json]    # local: pure-Python reference, writes result.json (or OUT.json)

On a serverless job the same baskets go through pyspark.pandas; before this release `groupby(...)[col].describe()`
raised PandasNotImplementedError. Locally the result.json numbers come from a pure-Python reference; the test also
runs the pandas-on-Spark call on local pyspark 4.3 and checks it against that reference.

One thing worth knowing: pandas-on-Spark quartiles come from Spark's percentile_approx, so they are observed basket
values (nearest rank, the smallest value with at least q of the baskets at or below it), while plain pandas
interpolates between neighbours. The reference reproduces the Spark rule, and result.json records both.

From each store's describe() row we set two bands for the checkout team:
  * coupon threshold = 75th percentile: baskets at or above it get the "big basket" $5-off-next-visit coupon;
  * bulk fence = Q3 + 1.5 * IQR: baskets above it are catering / bulk orders, flagged for a manager check
    instead of a coupon.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

STORES = ["downtown", "suburb", "campus"]
BASKETS = 40
STATS = ["count", "mean", "std", "min", "25%", "50%", "75%", "max"]
# store: (base basket $, spread step $, bulk orders as {basket index: value})
SHAPE = {
    "downtown": (18.0, 1.75, {7: 212.40}),            # quick lunch-and-dinner trips, one office catering order
    "suburb": (46.0, 3.10, {12: 268.95, 31: 241.10}),  # weekly family shops, two party orders
    "campus": (11.0, 0.85, {}),                        # small, tight baskets, no bulk orders
}


def rows() -> list[dict]:
    """40 checkout baskets per store (120 rows). Deterministic 'noise' so every run gives the same numbers."""
    out = []
    for si, store in enumerate(STORES):
        base, step, bulk = SHAPE[store]
        for i in range(BASKETS):
            value = bulk.get(i, base + step * ((i * 37 + si * 13) % 23) + ((i * 7) % 5) * 0.29)
            out.append({"store": store, "basket_id": f"{store[:3]}-{i:03d}", "basket": round(value, 2)})
    return out


def nearest_rank(sorted_vals: list[float], q: float) -> float:
    """Spark percentile_approx on small data: the smallest value with at least q of the rows at or below it."""
    return sorted_vals[max(0, math.ceil(q * len(sorted_vals)) - 1)]


def interpolated(sorted_vals: list[float], q: float) -> float:
    """Plain pandas (numpy 'linear') quantile, for comparison."""
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def describe_reference(data: list[dict]) -> dict:
    """{store: {count, mean, std, min, 25%, 50%, 75%, max}} like pandas-on-Spark groupby()[col].describe()."""
    res = {}
    for store in STORES:
        v = sorted(r["basket"] for r in data if r["store"] == store)
        n = len(v)
        mean = sum(v) / n
        std = math.sqrt(sum((x - mean) ** 2 for x in v) / (n - 1))
        res[store] = {"count": float(n), "mean": mean, "std": std, "min": v[0],
                      "25%": nearest_rank(v, 0.25), "50%": nearest_rank(v, 0.50), "75%": nearest_rank(v, 0.75),
                      "max": v[-1]}
    return res


def describe_pandas_on_spark(data: list[dict]) -> dict:
    """The new call. Runs on Databricks serverless (and on any pyspark with SeriesGroupBy.describe)."""
    import pyspark.pandas as ps
    psdf = ps.DataFrame(data)
    d = psdf.groupby("store")["basket"].describe().to_pandas()   # index store, columns count..max
    return {store: {s: float(d.loc[store, s]) for s in STATS} for store in STORES}


def summarise(desc: dict, data: list[dict]) -> dict:
    stores = []
    for store in STORES:
        d = desc[store]
        v = sorted(r["basket"] for r in data if r["store"] == store)
        iqr = d["75%"] - d["25%"]
        fence = d["75%"] + 1.5 * iqr
        bulk = [x for x in v if x > fence]
        coupons = [x for x in v if d["75%"] <= x <= fence]
        stores.append({
            "store": store,
            "describe": {s: round(d[s], 4) for s in STATS},
            "pandas_interpolated_quartiles": {f"{int(q * 100)}%": round(interpolated(v, q), 4)
                                              for q in (0.25, 0.5, 0.75)},
            "coupon_threshold": round(d["75%"], 2),
            "bulk_fence": round(fence, 2),
            "coupon_baskets": len(coupons),
            "bulk_baskets": len(bulk),
            "mean_minus_median": round(d["mean"] - d["50%"], 2),
            "takings": round(sum(v), 2),
        })
    return {"rows": len(data), "stats": STATS,
            "most_skewed": max(stores, key=lambda s: s["mean_minus_median"])["store"],
            "coupon_baskets": sum(s["coupon_baskets"] for s in stores),
            "bulk_baskets": sum(s["bulk_baskets"] for s in stores),
            "stores": stores}


def run_cloud_job() -> dict:
    """Serverless job entry: the pandas-on-Spark describe, plus where it ran."""
    import pyspark
    data = rows()
    try:   # pandas API on Spark prefers ANSI off; serverless may not allow changing it, which is fine
        from pyspark.sql import SparkSession
        SparkSession.builder.getOrCreate().conf.set("spark.sql.ansi.enabled", "false")
    except Exception:  # noqa: BLE001
        pass
    out = summarise(describe_pandas_on_spark(data), data)
    out["engine"] = f"pyspark.pandas SeriesGroupBy.describe (pyspark {pyspark.__version__})"
    return out


def main(argv: list[str]) -> int:
    if argv:  # serverless job: argv[0] is a Unity Catalog volume path for the result
        text = json.dumps(run_cloud_job(), sort_keys=True)
        print("DAY30_RESULT " + text)    # read back from the task's logs if the output file can't be read
        Path(argv[0]).write_text(text)
        return 0
    data = rows()
    out = {"engine": "pure-Python reference (local) / pyspark.pandas SeriesGroupBy.describe on a Databricks "
                     "serverless job (cloud)", **summarise(describe_reference(data), data)}
    (Path(__file__).resolve().parent / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("most_skewed", "coupon_baskets", "bulk_baskets")}),
          [(s["store"], s["coupon_threshold"], s["bulk_fence"], s["bulk_baskets"]) for s in out["stores"]])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
