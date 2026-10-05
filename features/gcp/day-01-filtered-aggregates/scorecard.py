"""Day 1 (Google Cloud): category scorecard with BigQuery's new WHERE-inside-aggregate syntax.

  python scorecard.py --local      # DuckDB, translating AGG(x WHERE c) to AGG(x) FILTER (WHERE c); writes result.json
  python scorecard.py --compare F  # compare BigQuery JSON output in file F with the local result
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGG_START = re.compile(r"\b(COUNT|AVG|SUM|MIN|MAX)\(", re.I)


def bigquery_sql(table: str) -> str:
    return (HERE / "scorecard.sql").read_text().replace("{table}", table)


def to_duckdb(sql: str) -> str:
    """BigQuery's AGG(x WHERE c) is standard SQL's AGG(x) FILTER (WHERE c). Comments are dropped first."""
    sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--")).replace("`", "")
    out, i = [], 0
    for m in AGG_START.finditer(sql):
        if m.start() < i:
            continue
        depth, j = 1, m.end()
        while depth:  # find the matching closing parenthesis
            depth += {"(": 1, ")": -1}.get(sql[j], 0)
            j += 1
        inner = sql[m.end():j - 1]
        k = _top_level_where(inner)
        out.append(sql[i:m.start()])
        out.append(f"{m[1]}({inner[:k].strip()}) FILTER (WHERE {inner[k + 5:].strip()})" if k >= 0 else sql[m.start():j])
        i = j
    return "".join(out) + sql[i:]


def _top_level_where(s: str) -> int:
    depth = 0
    for idx, ch in enumerate(s):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0 and s[idx:idx + 7].upper() == " WHERE ":
            return idx + 1
    return -1


def normalise(rows) -> list[dict]:
    keys = ["category", "products", "grade_a_or_b", "with_high_risk_additives", "avg_score",
            "avg_score_organic", "avg_score_conventional"]
    out = []
    for r in rows:
        r = dict(zip(keys, r)) if not isinstance(r, dict) else r
        out.append({k: (r[k] if k == "category" else (None if r[k] in (None, "") else
                    (round(float(r[k]), 1) if k.startswith("avg") else int(r[k])))) for k in keys})
    return out


def run_local() -> list[dict]:
    import duckdb
    con = duckdb.connect()
    con.execute(f"CREATE TABLE products AS SELECT * FROM read_csv_auto('{HERE / 'data' / 'products.csv'}', "
                "types={'barcode': 'VARCHAR'})")
    return normalise(con.execute(to_duckdb(bigquery_sql("products"))).fetchall())


def summarise(rows: list[dict]) -> dict:
    organic_gap = [r["avg_score_organic"] - r["avg_score_conventional"] for r in rows
                   if r["avg_score_organic"] is not None and r["avg_score_conventional"] is not None]
    return {
        "products": sum(r["products"] for r in rows),
        "categories": len(rows),
        "best_category": rows[0]["category"],
        "share_grade_a_or_b_pct": round(100 * sum(r["grade_a_or_b"] for r in rows) / sum(r["products"] for r in rows), 1),
        "with_high_risk_additives": sum(r["with_high_risk_additives"] for r in rows),
        "avg_organic_advantage_points": round(sum(organic_gap) / len(organic_gap), 1),
    }


if __name__ == "__main__":
    local = run_local()
    if len(sys.argv) > 2 and sys.argv[1] == "--compare":
        text = Path(sys.argv[2]).read_text()
        start = text.find("[")  # bq may print a warning line before the JSON
        try:
            cloud = normalise(json.loads(text[start:]))
        except (ValueError, KeyError) as e:
            print(f"::error title=Unreadable BigQuery output::{type(e).__name__}: {text[:300]!r}")
            raise SystemExit(1)
        for r in cloud:
            print(r)
        if cloud != local:
            diff = [(c, l) for c, l in zip(cloud, local) if c != l] or [("rows", len(cloud), len(local))]
            print(f"::error title=BigQuery result differs from local::{json.dumps(diff)[:900]}")
            raise SystemExit(1)
        print(f"::notice title=Google Cloud day 1::WHERE-in-aggregate ran on BigQuery: {json.dumps(summarise(cloud))}")
    else:
        (HERE / "result.json").write_text(json.dumps({"engine": "duckdb (local) / BigQuery (cloud)", **summarise(local),
                                                      "by_category": local}, indent=2) + "\n")
        print(json.dumps(summarise(local), indent=2))
