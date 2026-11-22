"""Day 48 (Google Cloud): basket lines from nested order JSON with **JSON_FLATTEN** (GA in the BigQuery release
notes of 2026-11-11). A grocery app sends each order's `lines` as JSON in whatever shape the cart had: a flat list,
a meal deal grouped as a nested array, a bundle inside a bundle, a single line sent as a bare object, or nothing.
`JSON_FLATTEN(lines)` turns every shape into one `ARRAY<JSON>` of line objects, so counting units and pricing the
basket is one plain `UNNEST`, with no recursive UDF and no guessing the nesting depth.

  python basket.py                  # local: the pure-Python reference; writes result.json
  python basket.py --sql            # print the BigQuery SQL (orders inlined, nothing created)
  python basket.py --compare OUT    # check bq's JSON output against the reference (cloud.sh)

JSON_FLATTEN's rule (reference page): the result holds every non-array value that sits directly in the input or
inside consecutively nested arrays; a non-array input is wrapped (`JSON '1'` -> `[1]`); arrays inside an object
stay inside it (`{"a": [[1]]}` -> `[{"a": [[1]]}]`); SQL NULL in, SQL NULL out.

Must hold (else the run fails): 8 rows; per order, the flattened array equals the reference element for element and
in order, and line count, units, basket total (cents) and the SKU list match.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def orders() -> list[dict]:
    return [json.loads(line) for line in (HERE / "data" / "orders.jsonl").read_text().splitlines() if line.strip()]


def flatten(value):
    """Pure-Python JSON_FLATTEN: None (SQL NULL) -> None; arrays are opened recursively, anything else is kept."""
    if value is None:
        return None
    if not isinstance(value, list):
        return [value]
    out = []
    for v in value:
        out.extend(flatten(v) if isinstance(v, list) else [v])
    return out


def expected() -> dict[int, dict]:
    res = {}
    for o in orders():
        flat = flatten(o["lines"])
        objs = [x for x in (flat or []) if isinstance(x, dict)]
        res[o["order_id"]] = {
            "flat": flat,
            "line_count": len(flat or []),
            "units": sum(x["qty"] for x in objs) if objs else None,
            "total_cents": sum(x["qty"] * x["cents"] for x in objs) if objs else None,
            "skus": ",".join(x["sku"] for x in objs) if objs else None,
        }
    return res


def _lines_sql(lines) -> str:
    if lines is None:
        return "CAST(NULL AS JSON)"
    text = json.dumps(lines, separators=(",", ":"))
    assert "'" not in text and "\\" not in text
    return f"PARSE_JSON('{text}')"


def sql() -> str:
    rows = ",\n    ".join(f"STRUCT({o['order_id']} AS order_id, {_lines_sql(o['lines'])} AS lines)" for o in orders())
    return f"""-- Day 48: basket lines from nested order JSON with JSON_FLATTEN (GA 2026-11-11).
-- Orders are inlined; nothing is created. Each line is a JSON object {{sku, qty, cents}}.
WITH orders AS (
  SELECT * FROM UNNEST([
    {rows}
  ])
)
SELECT
  order_id,
  TO_JSON_STRING(JSON_FLATTEN(lines)) AS flat,
  (SELECT COUNT(*) FROM UNNEST(JSON_FLATTEN(lines)) AS l) AS line_count,
  (SELECT SUM(LAX_INT64(l.qty)) FROM UNNEST(JSON_FLATTEN(lines)) AS l) AS units,
  (SELECT SUM(LAX_INT64(l.qty) * LAX_INT64(l.cents)) FROM UNNEST(JSON_FLATTEN(lines)) AS l) AS total_cents,
  (SELECT STRING_AGG(LAX_STRING(l.sku), ',' ORDER BY pos)
     FROM UNNEST(JSON_FLATTEN(lines)) AS l WITH OFFSET AS pos) AS skus
FROM orders
ORDER BY order_id
"""


# ---------- cloud output
def _redact(text: str) -> str:
    return re.sub(r"\d{6,}", "<number>", text.replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>"))


def read_rows(path: str) -> list[dict] | None:
    text = Path(path).read_text()
    dec = json.JSONDecoder()
    for i in (k for k, ch in enumerate(text) if ch == "["):
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, list) and all(isinstance(r, dict) for r in obj):
            return obj
    return None


def _int(v):
    return None if v is None else int(v)


def _flat(v):
    """bq returns TO_JSON_STRING's text as a string; SQL NULL comes back as null (or the text 'null')."""
    if v is None:
        return None
    return json.loads(v) if isinstance(v, str) else v


def check(rows: list[dict]) -> list[str]:
    exp, got, problems = expected(), {}, []
    for r in rows:
        try:
            got[int(r.get("order_id"))] = r
        except (TypeError, ValueError):
            problems.append(f"bad order_id {r.get('order_id')!r}")
    if sorted(got) != sorted(exp):
        problems.append(f"orders back {sorted(got)}, expected {sorted(exp)}")
    for n, e in exp.items():
        if n not in got:
            continue
        g = got[n]
        try:
            flat = _flat(g.get("flat"))
        except ValueError:
            problems.append(f"order {n}: flat is not JSON: {_redact(str(g.get('flat')))[:100]}")
            continue
        if flat != e["flat"]:
            problems.append(f"order {n}: JSON_FLATTEN gave {_redact(json.dumps(flat))[:150]}, "
                            f"expected {json.dumps(e['flat'])[:150]}")
        for k in ("line_count", "units", "total_cents"):
            if _int(g.get(k)) != e[k]:
                problems.append(f"order {n}: {k} {g.get(k)!r}, expected {e[k]!r}")
        if (g.get("skus") or None) != e["skus"]:
            problems.append(f"order {n}: skus {g.get('skus')!r}, expected {e['skus']!r}")
    return problems


def compare(out_path: str) -> int:
    rows = read_rows(out_path)
    if rows is None:
        print(f"::error title=Unreadable BigQuery output::{_redact(Path(out_path).read_text()[:300])!r}")
        return 1
    problems = check(rows)
    if problems:
        print("::error title=Google Cloud day 48 output check failed::" + "; ".join(problems)[:900])
        return 1
    s = summarise()
    print(f"::notice title=Google Cloud day 48::JSON_FLATTEN turned {s['orders']} orders' line JSON (flat, nested "
          f"meal deals up to {s['max_depth']} levels, a bare object, empty bundles, NULL) into {s['lines']} line "
          f"objects in one query; every order matched the Python reference element for element: {s['units']} units, "
          f"basket total {s['total_cents']} cents, the nested object's own arrays kept intact, NULL stayed NULL.")
    return 0


def _depth(v) -> int:
    return 1 + max((_depth(x) for x in v), default=0) if isinstance(v, list) else 0


def summarise() -> dict:
    exp = expected()
    os_ = orders()
    return {"orders": len(exp), "lines": sum(e["line_count"] for e in exp.values()),
            "units": sum(e["units"] or 0 for e in exp.values()),
            "total_cents": sum(e["total_cents"] or 0 for e in exp.values()),
            "max_depth": max(_depth(o["lines"]) for o in os_),
            "orders_nested": sum(_depth(o["lines"]) > 1 for o in os_),
            "null_orders": sum(e["flat"] is None for e in exp.values()),
            "queries": 1, "bytes_scanned": 0,
            "per_order": {str(n): {k: e[k] for k in ("line_count", "units", "total_cents")} for n, e in exp.items()}}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql())
    elif mode == "--compare":
        raise SystemExit(compare(sys.argv[2]))
    else:
        s = summarise()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "pure-Python JSON_FLATTEN reference (local) / BigQuery JSON_FLATTEN (cloud)", **s},
            indent=2) + "\n")
        print(json.dumps(s, indent=2))
