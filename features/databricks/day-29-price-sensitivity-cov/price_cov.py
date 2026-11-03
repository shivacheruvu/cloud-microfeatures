"""Day 29 (Databricks): per-store price sensitivity of oat milk from one pandas-API-on-Spark call,
`psdf.groupby("store")[["price", "units", "promo"]].cov()` (DataFrameGroupBy.cov, new on serverless 2026-08-17).

  python price_cov.py [OUT.json]    # local: pure-Python reference, writes result.json (or OUT.json)

On a serverless job the same data goes through pyspark.pandas; before this release `groupby(...).cov()` raised
PandasNotImplementedError, so a per-group covariance matrix meant hand-writing covar_samp for every column pair.
Locally the result.json numbers come from a pure-Python sample covariance (ddof=1); the test also runs the
pandas-on-Spark call on local pyspark (OSS has it from 4.3) and checks it against that reference. From each store's
matrix we read the price slope cov(price, units) / var(price): extra units sold per $1 price cut.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

STORES = ["east", "west", "north"]
DAYS = 28
COLS = ["price", "units", "promo"]
# store: (base demand, units lost per $1, promo price, regular price, regular price from day 14 or None)
SHAPE = {
    "east": (62, 8, 3.49, 4.49, None),    # price-insensitive regulars
    "west": (95, 16, 3.49, 4.49, None),   # deal seekers
    "north": (70, 10, 3.99, 4.49, 4.99),  # price went up mid-month; smaller promos
}


def rows() -> list[dict]:
    """Daily oat-milk sales: 3 stores x 28 days. Promo on days 5 and 6 of each week. Deterministic 'noise'."""
    out = []
    for si, store in enumerate(STORES):
        base, slope, promo_price, regular, regular_late = SHAPE[store]
        for day in range(DAYS):
            promo = 1 if day % 7 in (5, 6) else 0
            reg = regular_late if (regular_late and day >= 14) else regular
            price = promo_price if promo else reg
            noise = (day * 37 + si * 11) % 7 - 3
            units = round(base - slope * price + 6 * promo + noise)
            out.append({"store": store, "day": day, "price": price, "units": int(units), "promo": promo})
    return out


def cov_reference(data: list[dict]) -> dict:
    """Sample covariance (ddof=1) per store, like pandas groupby().cov(): {store: {col: {col: value}}}."""
    res = {}
    for store in STORES:
        g = [r for r in data if r["store"] == store]
        n = len(g)
        mean = {c: sum(r[c] for r in g) / n for c in COLS}
        res[store] = {a: {b: sum((r[a] - mean[a]) * (r[b] - mean[b]) for r in g) / (n - 1) for b in COLS}
                      for a in COLS}
    return res


def cov_pandas_on_spark(data: list[dict]) -> dict:
    """The new call. Runs on Databricks serverless (and on any pyspark with DataFrameGroupBy.cov)."""
    import pyspark.pandas as ps
    psdf = ps.DataFrame(data)[["store"] + COLS]
    m = psdf.groupby("store")[COLS].cov().to_pandas()   # MultiIndex (store, column) x columns
    return {store: {a: {b: float(m.loc[(store, a), b]) for b in COLS} for a in COLS} for store in STORES}


def summarise(cov: dict, data: list[dict]) -> dict:
    stores = []
    for store in STORES:
        c = cov[store]
        g = [r for r in data if r["store"] == store]
        slope = c["price"]["units"] / c["price"]["price"]
        corr = c["price"]["units"] / math.sqrt(c["price"]["price"] * c["units"]["units"])
        pp = c["price"]["promo"] / math.sqrt(c["price"]["price"] * c["promo"]["promo"])
        # two-variable least squares from the same matrix: [b_price, b_promo] = Sxx^-1 Sxy
        det = c["price"]["price"] * c["promo"]["promo"] - c["price"]["promo"] ** 2
        separable = det > 1e-9 * c["price"]["price"] * c["promo"]["promo"]
        if separable:
            b_price = (c["promo"]["promo"] * c["price"]["units"] - c["price"]["promo"] * c["promo"]["units"]) / det
            b_promo = (c["price"]["price"] * c["promo"]["units"] - c["price"]["promo"] * c["price"]["units"]) / det
        promo = [r["units"] for r in g if r["promo"]]
        regular = [r["units"] for r in g if not r["promo"]]
        stores.append({
            "store": store,
            "days": len(g),
            "units": sum(r["units"] for r in g),
            "cov": {a: {b: round(c[a][b], 6) for b in COLS} for a in COLS},
            "units_per_dollar_cut": round(-slope, 2),
            "price_units_corr": round(corr, 3),
            "promo_lift_units": round(sum(promo) / len(promo) - sum(regular) / len(regular), 2),
            "price_promo_corr": round(pp, 3),
            "separable": separable,
            "price_effect_without_promo": round(-b_price, 2) if separable else None,
            "promo_effect_at_same_price": round(b_promo, 2) if separable else None,
        })
    ranked = sorted(stores, key=lambda s: -s["units_per_dollar_cut"])
    return {"rows": len(data), "most_price_sensitive": ranked[0]["store"],
            "least_price_sensitive": ranked[-1]["store"], "stores": stores}


def run_cloud_job() -> dict:
    """Serverless job entry: the pandas-on-Spark covariance, plus where it ran."""
    import pyspark
    data = rows()
    try:   # pandas API on Spark prefers ANSI off; serverless may not allow changing it, which is fine
        from pyspark.sql import SparkSession
        SparkSession.builder.getOrCreate().conf.set("spark.sql.ansi.enabled", "false")
    except Exception:  # noqa: BLE001
        pass
    out = summarise(cov_pandas_on_spark(data), data)
    out["engine"] = f"pyspark.pandas DataFrameGroupBy.cov (pyspark {pyspark.__version__})"
    return out


def main(argv: list[str]) -> int:
    if argv:  # serverless job: argv[0] is a Unity Catalog volume path for the result
        text = json.dumps(run_cloud_job(), sort_keys=True)
        print("DAY29_RESULT " + text)    # read back from the task's logs if the output file can't be read
        Path(argv[0]).write_text(text)
        return 0
    data = rows()
    out = {"engine": "pure-Python sample covariance reference (local) / pyspark.pandas DataFrameGroupBy.cov on a "
                     "Databricks serverless job (cloud)", **summarise(cov_reference(data), data)}
    (Path(__file__).resolve().parent / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("most_price_sensitive", "least_price_sensitive")}),
          [(s["store"], s["units_per_dollar_cut"], s["price_units_corr"]) for s in out["stores"]])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
