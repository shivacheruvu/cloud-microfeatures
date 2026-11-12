"""Day 38 (Google Cloud): count every picked item in nested tote manifests with BigQuery's JSON_FLATTEN
(GA, 2026-11-11).

  python tote_manifest.py                  # local: plain-Python model of JSON_FLATTEN; writes result.json
  python tote_manifest.py --sql            # print the main BigQuery query (orders inlined, no table needed)
  python tote_manifest.py --edge-sql       # print the edge-case query
  python tote_manifest.py --compare OUT    # check bq's JSON output of the main query against the local model
  python tote_manifest.py --edge-compare OUT   # report the edge cases next to the local model (never fails)

Eight online orders (data/orders.jsonl) pack items as deep as the picker happened to: tote -> bag -> item, an item
straight in a tote, a bag inside a bag, or an express order whose `totes` is a flat item list. JSON_FLATTEN finds every
item whatever the depth; the fixed-depth JSON_QUERY_ARRAY unnest (the way before) is computed next to it to show what
it silently drops. Every number per order must equal the local model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NUM_COLS = ("flat_items", "flat_units", "flat_value_cents", "flat_non_objects", "items_with_subs",
            "fixed_items", "fixed_units", "fixed_value_cents")


def load_orders() -> list[dict]:
    return [json.loads(line) for line in (HERE / "data/orders.jsonl").read_text().splitlines() if line.strip()]


def json_flatten(value):
    """Local model of JSON_FLATTEN: every non-array value directly in `value` or inside consecutively nested arrays,
    in document order. Objects are leaves (arrays inside them stay put). SQL NULL (None here) -> None."""
    if value is None:
        return None
    out = []

    def walk(v):
        if isinstance(v, list):
            for x in v:
                walk(x)
        else:
            out.append(v)
    walk(value)
    return out


def fixed_depth(totes) -> list:
    """The pre-JSON_FLATTEN query: tote -> bag -> item with JSON_QUERY_ARRAY, keeping only objects at level 3."""
    if not isinstance(totes, list):
        return []
    return [it for tote in totes if isinstance(tote, list) for bag in tote if isinstance(bag, list)
            for it in bag if isinstance(it, dict)]


def order_row(o: dict) -> dict:
    totes = o.get("totes")
    flat = json_flatten(totes) or []
    objs = [it for it in flat if isinstance(it, dict)]
    fixed = fixed_depth(totes)
    return {
        "order_id": o["order_id"], "store": o["store"], "no_totes": totes is None,
        "flat_items": len(flat), "flat_units": sum(it["qty"] for it in objs),
        "flat_value_cents": sum(it["qty"] * it["price_cents"] for it in objs),
        "flat_non_objects": len(flat) - len(objs), "items_with_subs": sum("subs" in it for it in objs),
        "sku_sequence": ",".join(it["sku"] for it in objs),
        "fixed_items": len(fixed), "fixed_units": sum(it["qty"] for it in fixed),
        "fixed_value_cents": sum(it["qty"] * it["price_cents"] for it in fixed),
    }


def local_rows(orders: list[dict]) -> list[dict]:
    return sorted((order_row(o) for o in orders), key=lambda r: r["order_id"])


def depth_counts(orders: list[dict]) -> dict:
    """How many items sit at each nesting depth (1 = directly in `totes`)."""
    counts: dict[int, int] = {}

    def walk(v, d):
        if isinstance(v, list):
            for x in v:
                walk(x, d + 1)
        else:
            counts[d] = counts.get(d, 0) + 1
    for o in orders:
        if o.get("totes") is not None:
            walk(o["totes"], 0)
    return {f"depth {k}": counts[k] for k in sorted(counts)}


def summarise(rows: list[dict], orders: list[dict] | None = None) -> dict:
    tot = {c: sum(int(r[c]) for r in rows) for c in NUM_COLS}
    picked = [r for r in rows if str(r["no_totes"]).lower() != "true"]
    short = [r["order_id"] for r in picked if int(r["fixed_items"]) < int(r["flat_items"])]
    out = {
        "orders": len(rows), "orders_with_totes": len(picked),
        "items": {"json_flatten": tot["flat_items"], "fixed_depth_unnest": tot["fixed_items"]},
        "units": {"json_flatten": tot["flat_units"], "fixed_depth_unnest": tot["fixed_units"]},
        "order_value_usd": {"json_flatten": round(tot["flat_value_cents"] / 100, 2),
                            "fixed_depth_unnest": round(tot["fixed_value_cents"] / 100, 2)},
        "missed_by_fixed_depth": {"items": tot["flat_items"] - tot["fixed_items"],
                                  "units": tot["flat_units"] - tot["fixed_units"],
                                  "value_usd": round((tot["flat_value_cents"] - tot["fixed_value_cents"]) / 100, 2),
                                  "orders_short": short},
        "items_with_subs_not_double_counted": tot["items_with_subs"],
        "by_order": {r["order_id"]: {"flatten": int(r["flat_items"]), "fixed": int(r["fixed_items"])}
                     for r in rows},
    }
    if orders is not None:
        out["items_by_depth"] = depth_counts(orders)
    return out


def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def sql(orders: list[dict]) -> str:
    rows = ",\n  ".join(f"STRUCT({_q(o['order_id'])} AS order_id, {_q(json.dumps(o, separators=(',', ':')))} AS doc)"
                        for o in orders)
    return (HERE / "tote_manifest.sql").read_text().replace("{orders}", f"[\n  {rows}\n]")


EDGE_CASES = {  # label -> local model's answer (as TO_JSON_STRING would print it)
    "doc: scalar": "[1]", "doc: array with null": "[1,2,null]", "doc: nested arrays": "[1,2,3]",
    "doc: array inside object": '[{"a":[[1]]}]', "doc: mixed": '[1,2,3,{"a":4},true]',
    "empty array": "[]", "arrays of empty arrays": "[1]", "JSON null": "[null]", "SQL NULL": "null",
}


def check_edge_model() -> list[str]:
    """The local model reproduces its own table (and the five documented examples)."""
    inputs = {"doc: scalar": 1, "doc: array with null": [1, 2, None], "doc: nested arrays": [[[1]], 2, [3]],
              "doc: array inside object": {"a": [[1]]}, "doc: mixed": [[[1, 2], 3], {"a": 4}, True],
              "empty array": [], "arrays of empty arrays": [[], [[]], [1]], "JSON null": "__json_null__",
              "SQL NULL": None}
    bad = []
    for label, v in inputs.items():
        got = [None] if v == "__json_null__" else json_flatten(v)
        if json.dumps(got, separators=(",", ":")) != EDGE_CASES[label]:
            bad.append(label)
    return bad


def check_cloud(rows: list[dict], orders: list[dict]) -> list[str]:
    want = {r["order_id"]: r for r in local_rows(orders)}
    errors = []
    if sorted(r["order_id"] for r in rows) != sorted(want):
        errors.append(f"BigQuery returned orders {sorted(r['order_id'] for r in rows)}, local {sorted(want)}")
    for r in rows:
        w = want.get(r["order_id"])
        if w is None:
            continue
        for c in NUM_COLS:
            if int(r[c]) != w[c]:
                errors.append(f"{r['order_id']} {c}: BigQuery {r[c]}, local {w[c]}")
        if str(r["no_totes"]).lower() != str(w["no_totes"]).lower():
            errors.append(f"{r['order_id']} no_totes: BigQuery {r['no_totes']}, local {w['no_totes']}")
    return errors


def read_bq_json(path: str) -> list[dict]:
    raw = Path(path).read_text()
    start = raw.find("[")  # bq may print a warning line before the JSON
    return json.loads(raw[start:]) if start >= 0 else []


def _norm(s) -> str:
    try:
        return json.dumps(json.loads(s), separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return str(s)


def main(*argv: str) -> int:
    orders = load_orders()
    if argv[:1] == ("--sql",):
        print(sql(orders))
    elif argv[:1] == ("--edge-sql",):
        print((HERE / "edge_cases.sql").read_text())
    elif argv[:1] == ("--compare",) and len(argv) > 1:
        try:
            rows = read_bq_json(argv[1])
            errors = check_cloud(rows, orders)
        except (ValueError, KeyError, TypeError) as e:
            print(f"::error title=Day 38 unreadable BigQuery output::{e!r}"[:500])
            return 1
        for e in errors[:10]:
            print(f"::error title=Day 38 JSON_FLATTEN result differs from local::{e[:400]}")
        if errors:
            return 1
        s = summarise(rows)
        local_seq = {r["order_id"]: r["sku_sequence"] for r in local_rows(orders)}
        same_order = sum(r["sku_sequence"] == local_seq[r["order_id"]] for r in rows)
        print("::notice title=Google Cloud day 38::JSON_FLATTEN ran in BigQuery and matched local on all "
              f"{len(rows)} orders: {s['items']['json_flatten']} items / {s['units']['json_flatten']} units / "
              f"${s['order_value_usd']['json_flatten']:.2f}. The fixed-depth JSON_QUERY_ARRAY unnest finds only "
              f"{s['items']['fixed_depth_unnest']} items (${s['order_value_usd']['fixed_depth_unnest']:.2f}); short on "
              f"{', '.join(s['missed_by_fixed_depth']['orders_short'])}. {s['items_with_subs_not_double_counted']} "
              f"items with substitutes counted once. Item order equals document order on {same_order}/{len(rows)} "
              "orders."[:900])
    elif argv[:1] == ("--edge-compare",) and len(argv) > 1:
        try:
            got = {r["label"]: r.get("flattened") for r in read_bq_json(argv[1])}
        except (ValueError, KeyError, TypeError) as e:
            print(f"::warning title=Day 38 edge cases unreadable::{e!r}"[:400])
            return 0
        same = [k for k in EDGE_CASES if k in got and _norm(got[k]) == _norm(EDGE_CASES[k])]
        diff = {k: got.get(k) for k in EDGE_CASES if k not in same}
        kind = "notice" if not diff else "warning"
        print(f"::{kind} title=Day 38 JSON_FLATTEN edge cases::{len(same)}/{len(EDGE_CASES)} as the local model; "
              f"BigQuery differs on: {json.dumps(diff)}"[:700])
    else:
        bad = check_edge_model()
        if bad:
            print(f"local model fails its own edge cases: {bad}")
            return 1
        summary = summarise(local_rows(orders), orders)
        (HERE / "result.json").write_text(json.dumps({"engine": "plain-Python JSON_FLATTEN model (local) / BigQuery "
                                                      "JSON_FLATTEN (cloud)", **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
