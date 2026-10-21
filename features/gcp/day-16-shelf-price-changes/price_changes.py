"""Day 16 (Google Cloud): a shelf-price change log straight from BigQuery's CHANGES table function (GA). A week of
price edits (load, supplier increase on dairy, markdown, a discontinued item, a new item) runs as separate commits on
a table with enable_change_history = TRUE; one CHANGES query returns every insert, update (as an old/new pair) and
delete, and the runner turns that into the "what changed on the shelf" report. No audit table, no triggers.

  python price_changes.py --local          # plain Python replays the same edits; writes result.json
  python price_changes.py --sql setup P.DS  # print the setup script (CREATE + DML) for dataset P.DS
  python price_changes.py --sql changes P.DS
  python price_changes.py --compare OUT    # check bq's JSON output of the CHANGES query against local

The cloud check (cloud.sh, GitHub Actions):
  * errors: any change row (commit, SKU, price, change type, is-for-update) in one result and not the other
The table lives in a short-lived dataset that cloud.sh deletes. SKUs and prices are made up.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ITEMS = [("P01", "Whole milk 1 gal", "dairy", 389), ("P02", "Greek yogurt 32 oz", "dairy", 549),
         ("P03", "Cheddar block 8 oz", "dairy", 429), ("P04", "Sourdough loaf", "bakery", 499),
         ("P05", "Bananas per lb", "produce", 69), ("P06", "Gala apples 3 lb", "produce", 449),
         ("P07", "Oat milk 64 oz", "plant-based", 479), ("P08", "Eggs dozen", "eggs", 329),
         ("P09", "Rye crackers", "snacks", 359), ("P10", "Coffee beans 12 oz", "pantry", 899)]
NEW_ITEM = ("P11", "Pumpkin bread", "bakery", 599)
CATEGORY = {i[0]: i[2] for i in ITEMS + [NEW_ITEM]}
# Each step is one commit. (label, SQL, Python replay over {sku: row})
STEPS = [
    ("Monday load: 10 SKUs",
     "INSERT INTO `{ds}.shelf_prices` (sku, name, category, price_cents) VALUES\n"
     + ",\n".join(f"  ('{s}', '{n}', '{c}', {p})" for s, n, c, p in ITEMS),
     lambda t: t.update({s: {"sku": s, "price_cents": p} for s, _, _, p in ITEMS})),
    ("Tuesday: supplier increase, dairy +20c",
     "UPDATE `{ds}.shelf_prices` SET price_cents = price_cents + 20 WHERE category = 'dairy'",
     lambda t: [r.update(price_cents=r["price_cents"] + 20) for s, r in t.items() if CATEGORY[s] == "dairy"]),
    ("Wednesday: weekend markdown, P03 and P07 -50c",
     "UPDATE `{ds}.shelf_prices` SET price_cents = price_cents - 50 WHERE sku IN ('P03', 'P07')",
     lambda t: [t[s].update(price_cents=t[s]["price_cents"] - 50) for s in ("P03", "P07")]),
    ("Thursday: P09 discontinued",
     "DELETE FROM `{ds}.shelf_prices` WHERE sku = 'P09'",
     lambda t: t.pop("P09")),
    ("Friday: new seasonal item P11",
     "INSERT INTO `{ds}.shelf_prices` (sku, name, category, price_cents) VALUES "
     f"('{NEW_ITEM[0]}', '{NEW_ITEM[1]}', '{NEW_ITEM[2]}', {NEW_ITEM[3]})",
     lambda t: t.update({NEW_ITEM[0]: {"sku": NEW_ITEM[0], "price_cents": NEW_ITEM[3]}})),
]


def _sections() -> dict[str, str]:
    out = {}
    for block in (HERE / "changes.sql").read_text().split("-- name: ")[1:]:
        name, body = block.split("\n", 1)
        out[name.strip()] = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("--")).strip()
    return out


def bigquery_sql(part: str, ds: str = "DATASET") -> str:
    """ds: the short-lived dataset as project.dataset (cloud.sh passes it; never printed)."""
    sql = _sections()[part].replace("{steps}", ";\n".join(s for _, s, _ in STEPS) + ";")
    return sql.replace("{ds}", ds)


def run_local() -> list[dict]:
    """Replay the steps in Python and emit the rows CHANGES documents: INSERT for new rows, DELETE for removed rows,
    and for an updated row the old row as DELETE (is_for_update) plus the new row as UPDATE."""
    table: dict[str, dict] = {}
    out = []
    for n, (_, _, apply) in enumerate(STEPS, start=1):
        before = {k: dict(v) for k, v in table.items()}
        apply(table)
        for sku in sorted(set(before) | set(table)):
            b, a = before.get(sku), table.get(sku)
            if b is None:
                out.append((n, sku, a["price_cents"], "INSERT", False))
            elif a is None:
                out.append((n, sku, b["price_cents"], "DELETE", False))
            elif a != b:
                out.append((n, sku, b["price_cents"], "DELETE", True))
                out.append((n, sku, a["price_cents"], "UPDATE", False))
    keys = ("commit_no", "sku", "price_cents", "change_type", "is_for_update")
    return [dict(zip(keys, r)) for r in sorted(out, key=lambda r: (r[0], r[1], r[3]))]


def _norm(r: dict) -> tuple:
    flag = r.get("is_for_update")
    flag = flag if isinstance(flag, bool) else str(flag).lower() == "true"   # bq's JSON gives BOOL as "true"
    return int(r["commit_no"]), r["sku"], int(r["price_cents"]), r["change_type"], flag


def read_bq(path: str) -> list[dict]:
    """The rows of the CHANGES query; bq may print a warning line before the JSON array."""
    text = Path(path).read_text()
    dec, i, found = json.JSONDecoder(), text.find("["), None
    while i >= 0:
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i = text.find("[", i + 1)
            continue
        if isinstance(obj, list) and (not obj or (isinstance(obj[0], dict) and "change_type" in obj[0])):
            found = obj
        i = text.find("[", end)
    if found is None:
        raw = text[:500].replace(os.environ.get("GCP_PROJECT_ID") or "\0", "<project>")
        print(f"::error title=Unreadable BigQuery output::no CHANGES rows found; {raw!r}")
        raise SystemExit(1)
    return found


def compare(cloud: list[dict], local: list[dict]) -> list[str]:
    try:
        c = Counter(_norm(r) for r in cloud)
    except (KeyError, TypeError, ValueError) as e:
        return [f"unreadable row: {e}"]
    l = Counter(_norm(r) for r in local)
    errors = [f"local only: {k}" for k in sorted((l - c).elements())]
    errors += [f"cloud only: {k}" for k in sorted((c - l).elements())]
    return errors


def summarise(rows: list[dict]) -> dict:
    rows = [dict(zip(("commit_no", "sku", "price_cents", "change_type", "is_for_update"), _norm(r))) for r in rows]
    types = Counter(r["change_type"] for r in rows)
    updates = []
    for r in rows:
        if r["change_type"] == "UPDATE":
            old = next(o for o in rows if o["commit_no"] == r["commit_no"] and o["sku"] == r["sku"]
                       and o["change_type"] == "DELETE" and o["is_for_update"])
            updates.append({"commit": r["commit_no"], "sku": r["sku"], "old_cents": old["price_cents"],
                            "new_cents": r["price_cents"]})
    per_sku = Counter(u["sku"] for u in updates)
    first = {r["sku"]: r["price_cents"] for r in rows if r["change_type"] == "INSERT"}
    last: dict[str, int | None] = dict(first)
    for u in updates:
        last[u["sku"]] = u["new_cents"]
    removed = sorted(r["sku"] for r in rows if r["change_type"] == "DELETE" and not r["is_for_update"])
    for s in removed:
        last[s] = None
    added = sorted(r["sku"] for r in rows if r["change_type"] == "INSERT" and r["commit_no"] > 1)
    net = {s: last[s] - first[s] for s in sorted(first) if last[s] is not None and last[s] != first[s]}
    commits = len({r["commit_no"] for r in rows})
    twice = sorted(s for s, n in per_sku.items() if n > 1)
    return {
        "commits": commits, "change_rows": len(rows), "by_type": dict(sorted(types.items())),
        "price_updates": len(updates), "skus_repriced": len(per_sku), "repriced_twice": twice,
        "removed": removed, "added": added, "net_change_cents": net,
        "steps": [label for label, _, _ in STEPS], "updates": updates,
        "headline": f"CHANGES returned {len(rows)} rows over {commits} commits: {len(updates)} price updates on "
                    f"{len(per_sku)} SKUs (each an old/new pair), {', '.join(twice)} repriced twice, "
                    f"{len(removed)} removed, {len(added)} added",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(bigquery_sql(sys.argv[2], *sys.argv[3:4]))
    elif mode == "--compare":
        cloud = read_bq(sys.argv[2])
        local = run_local()
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=CHANGES differs from local::{len(errors)} difference(s): {json.dumps(errors)[:800]}")
            raise SystemExit(1)
        s = summarise(cloud)
        print(f"::notice title=Google Cloud day 16::CHANGES on a table with enable_change_history matched the local "
              f"replay for all {len(local)} change rows: {s['headline']}; net change {json.dumps(s['net_change_cents'])}")
    else:
        summary = summarise(run_local())
        (HERE / "result.json").write_text(json.dumps({"engine": "Python replay (local) / BigQuery CHANGES (cloud)",
                                                      **summary}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
