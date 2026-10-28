"""Day 23 (Google Cloud): parse till-receipt lines into fields with BigQuery's REGEXP_EXTRACT_GROUPS.

  python receipt_parser.py                # local: Python `re` applies the same two patterns; writes result.json
  python receipt_parser.py --sql          # print the BigQuery SQL (lines inlined, no table needed)
  python receipt_parser.py --compare OUT  # check bq's JSON output against the local parse (cloud.sh)

A receipt line is either a packed item ("OAT MILK 64OZ 2 @ 3.49": name, optional pack size + unit, quantity,
price each) or a weighed item ("BANANAS 2.31 LB @ 0.59/LB": name, weight, price per lb). Anything else (coupons,
bag fees) is left for a person. REGEXP_EXTRACT_GROUPS returns every capturing group of one pattern in one call;
before it, each field needed its own REGEXP_EXTRACT with a single-group pattern.

The function's reference section hadn't rendered when this was built, so the SQL wraps the result in
TO_JSON_STRING and `groups_from_json` accepts both plausible shapes (an array of strings, or a struct whose fields
are the groups in order). cloud.sh reports which shape BigQuery returned.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
# RE2 and Python's `re` agree on these patterns (leftmost-first, no backreferences or lookarounds).
PACKED = r"^(.+?)(?: (\d+(?:\.\d+)?) ?(OZ|LB|CT|GAL|L|KG|G))? (\d+) @ (\d+\.\d{2})$"
WEIGHED = r"^(.+?) (\d+\.\d{2}) LB @ (\d+\.\d{2})/LB$"


def load_lines() -> list[dict]:
    with open(HERE / "data" / "receipt_lines.csv", newline="") as f:
        return [{"receipt": r["receipt"], "line_no": int(r["line_no"]), "line": r["line"]} for r in csv.DictReader(f)]


def groups_local(pattern: str, line: str) -> list | None:
    m = re.search(pattern, line)
    return list(m.groups()) if m else None


def groups_from_json(raw: str | None) -> list | None:
    """Normalise TO_JSON_STRING(REGEXP_EXTRACT_GROUPS(...)) to a list of groups, or None for no match."""
    if raw is None:
        return None
    v = json.loads(raw) if isinstance(raw, str) else raw
    if v is None:
        return None
    if isinstance(v, dict):
        v = list(v.values())
    if not isinstance(v, list) or not v or all(x is None or x == "" for x in v):
        return None
    return [None if x is None or x == "" else str(x) for x in v]  # an unmatched optional group may come back empty


def shape_of(raw: str | None) -> str:
    try:
        v = json.loads(raw) if raw else None
    except ValueError:
        return "unreadable"
    return {dict: "struct", list: "array"}.get(type(v), "null")


def to_item(packed: list | None, weighed: list | None) -> dict:
    if packed:
        name, size, unit, qty, price = packed
        amount = Decimal(qty) * Decimal(price)
        return {"kind": "packed", "name": name, "size": size, "unit": unit, "qty": int(qty),
                "price": price, "amount_cents": int(amount * 100)}
    if weighed:
        name, lb, price = weighed
        amount = (Decimal(lb) * Decimal(price)).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
        return {"kind": "weighed", "name": name, "size": lb, "unit": "LB", "qty": 1, "price": price,
                "amount_cents": int(amount * 100)}
    return {"kind": "unparsed"}


def parse_local(lines: list[dict]) -> list[dict]:
    return [{"receipt": l["receipt"], "line_no": l["line_no"],
             **to_item(groups_local(PACKED, l["line"]), groups_local(WEIGHED, l["line"]))} for l in lines]


def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def sql(lines: list[dict]) -> str:
    for p in (PACKED, WEIGHED):
        if "'" in p:
            raise ValueError("pattern must not contain a quote")
    rows = ",\n  ".join(f"STRUCT({_q(l['receipt'])} AS receipt, {int(l['line_no'])} AS line_no, "
                        f"{_q(l['line'])} AS line)" for l in lines)
    body = (HERE / "parse_lines.sql").read_text()
    return (body.replace("{packed}", PACKED).replace("{weighed}", WEIGHED)
            .replace("{lines}", f"[\n  {rows}\n]"))


def parse_cloud(rows: list[dict]) -> list[dict]:
    return [{"receipt": r["receipt"], "line_no": int(r["line_no"]),
             **to_item(groups_from_json(r.get("packed_groups")), groups_from_json(r.get("weighed_groups")))}
            for r in rows]


def summarise(lines: list[dict], items: list[dict]) -> dict:
    by_line = {(l["receipt"], l["line_no"]): l["line"] for l in lines}
    receipts = sorted({i["receipt"] for i in items})
    return {
        "lines": len(items),
        "packed": sum(i["kind"] == "packed" for i in items),
        "weighed": sum(i["kind"] == "weighed" for i in items),
        "no_pack_size": sum(i["kind"] == "packed" and i["size"] is None for i in items),
        "unparsed": [by_line[(i["receipt"], i["line_no"])] for i in items if i["kind"] == "unparsed"],
        "parsed_total_by_receipt": {r: round(sum(i.get("amount_cents", 0) for i in items if i["receipt"] == r)
                                             / 100, 2) for r in receipts},
        "regex_calls_per_line": {"REGEXP_EXTRACT_GROUPS": 2, "REGEXP_EXTRACT (one per field)": 8},
    }


if __name__ == "__main__":
    lines = load_lines()
    local = parse_local(lines)
    if len(sys.argv) > 1 and sys.argv[1] == "--sql":
        print(sql(lines))
    elif len(sys.argv) > 2 and sys.argv[1] == "--compare":
        raw = Path(sys.argv[2]).read_text()
        start = raw.find("[")  # bq may print a warning line before the JSON
        try:
            rows = json.loads(raw[start:]) if start >= 0 else []
            cloud = parse_cloud(rows)
        except (ValueError, KeyError, TypeError) as e:
            print(f"::error title=Day 23 unreadable BigQuery output::{e!r}")
            raise SystemExit(1)
        errors = [f"{w['receipt']}/{w['line_no']}: BigQuery {g} vs local {w}" for g, w in zip(cloud, local) if g != w]
        if len(cloud) != len(local):
            errors.insert(0, f"BigQuery returned {len(cloud)} rows, local {len(local)}")
        for e in errors[:10]:
            print(f"::error title=Day 23 REGEXP_EXTRACT_GROUPS parse differs from local::{e[:400]}")
        if errors:
            raise SystemExit(1)
        shapes = sorted({shape_of(r.get("packed_groups")) for r in rows} | {shape_of(r.get("weighed_groups"))
                                                                            for r in rows})
        print("::notice title=Google Cloud day 23::REGEXP_EXTRACT_GROUPS parsed the receipt lines in BigQuery and "
              f"matched local (result shapes seen: {', '.join(shapes)}): "
              f"{json.dumps(summarise(lines, cloud))[:650]}")
    else:
        summary = summarise(lines, local)
        (HERE / "result.json").write_text(json.dumps({"engine": "Python re (local) / BigQuery REGEXP_EXTRACT_GROUPS "
                                                      "(cloud)", **summary}, indent=2) + "\n")
        print(json.dumps({**summary, "items": local}, indent=2))
