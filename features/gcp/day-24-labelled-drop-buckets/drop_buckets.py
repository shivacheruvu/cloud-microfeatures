"""Day 24 (Google Cloud): store drop buckets that are labelled the moment they exist.

  python drop_buckets.py                   # local: build every store's labels and lifecycle; writes result.json
  python drop_buckets.py --plan DIR RUN    # write <id>.labels.json / <id>.lifecycle.json into DIR (cloud.sh);
                                           # prints "id<TAB>labels file<TAB>lifecycle file" per store
  python drop_buckets.py --compare DIR     # check <id>.describe.json (gcloud storage buckets describe) in DIR
                                           # against the local plan (cloud.sh)

`gcloud storage buckets create` gained `--labels` / `--labels-file` (Cloud SDK, 2026-10-27), so a bucket is created
with its owner, cost center and retention labels in the same call: there is no window where a new bucket exists
unlabelled and a cost or ownership report misses it. Store attributes are free text, so they're normalised to
Cloud Storage label rules first (lowercase letters, digits, `_` and `-`; keys start with a letter; at most 63
characters).
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORES = HERE / "data" / "stores.json"
FIXED = {"program": "cloud-microfeatures", "feature": "day-24"}
ATTRS = ("name", "region", "owner", "feed", "cost_center")  # label key = "store_name" for name
KEY = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
VALUE = re.compile(r"^[a-z0-9_-]{0,63}$")
MAX = 63


def label_value(text: str) -> tuple[str, bool]:
    """Free text -> a Cloud Storage label value; also says whether it had to be cut to 63 characters."""
    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    v = re.sub(r"[^a-z0-9_]+", "-", ascii_text.lower()).strip("-")
    cut = len(v) > MAX
    if cut:
        v = v[:MAX].rstrip("-_")
    return v, cut


def labels_for(store: dict, run: str | None = None) -> tuple[dict, list[str]]:
    labels, cut = {"store": label_value(store["id"])[0]}, []
    for a in ATTRS:
        key = "store_name" if a == "name" else a
        labels[key], was_cut = label_value(store[a])
        if was_cut:
            cut.append(f"{store['id']}.{key}")
    labels["retention_days"] = str(int(store["retention_days"]))
    labels.update(FIXED)
    if run is not None:
        labels["run"] = label_value(run)[0]
    for k, v in labels.items():
        if not KEY.match(k) or not VALUE.match(v):
            raise ValueError(f"label {k}={v!r} breaks Cloud Storage label rules")
    return labels, cut


def lifecycle_for(store: dict) -> dict:
    return {"rule": [{"action": {"type": "Delete"}, "condition": {"age": int(store["retention_days"])}}]}


def stores(path: Path = STORES) -> list[dict]:
    return json.loads(path.read_text())


def plan(run: str | None = None) -> list[dict]:
    out = []
    for s in stores():
        labels, cut = labels_for(s, run)
        out.append({"id": s["id"], "labels": labels, "lifecycle": lifecycle_for(s), "cut": cut})
    return out


def write_plan(folder: Path, run: str) -> list[str]:
    lines = []
    for p in plan(run):
        lab, life = folder / f"{p['id']}.labels.json", folder / f"{p['id']}.lifecycle.json"
        lab.write_text(json.dumps(p["labels"], indent=1))
        life.write_text(json.dumps(p["lifecycle"]))
        lines.append(f"{p['id']}\t{lab}\t{life}")
    return lines


def _ages(described: dict) -> list[int]:
    life = described.get("lifecycle_config") or described.get("lifecycle") or {}
    rules = life.get("rule") or life.get("rules") or []
    return sorted(int(r.get("condition", {}).get("age", -1)) for r in rules)


def compare(described: dict[str, dict], expected: list[dict]) -> list[str]:
    """described: store id -> `gcloud storage buckets describe --format=json` output."""
    errors = []
    for p in expected:
        d = described.get(p["id"])
        if not isinstance(d, dict):
            errors.append(f"{p['id']}: no describe output")
            continue
        got = {k: v for k, v in (d.get("labels") or {}).items() if k != "run"}
        want = {k: v for k, v in p["labels"].items() if k != "run"}
        if got != want:
            missing = sorted(set(want) - set(got))
            wrong = sorted(k for k in set(want) & set(got) if want[k] != got[k])
            errors.append(f"{p['id']}: labels differ (missing {missing}, wrong {wrong}, extra "
                          f"{sorted(set(got) - set(want))})")
        if _ages(d) != [r["condition"]["age"] for r in p["lifecycle"]["rule"]]:
            errors.append(f"{p['id']}: lifecycle delete ages {_ages(d)} vs local "
                          f"{[r['condition']['age'] for r in p['lifecycle']['rule']]}")
    return errors


def inventory(labelled: list[dict]) -> dict:
    """Buckets per owner, the report a label makes possible: owner -> {buckets, cost_centers, retention_days}."""
    inv: dict[str, dict] = {}
    for labels in labelled:
        o = inv.setdefault(labels["owner"], {"buckets": 0, "cost_centers": [], "retention_days": []})
        o["buckets"] += 1
        o["cost_centers"] = sorted(set(o["cost_centers"]) | {labels["cost_center"]})
        o["retention_days"] = sorted(set(o["retention_days"]) | {int(labels["retention_days"])})
    return dict(sorted(inv.items()))


def summarise(p: list[dict]) -> dict:
    return {
        "buckets": len(p),
        "labels_per_bucket": len(p[0]["labels"]),
        "create_calls_per_bucket": 1,
        "label_update_calls": 0,
        "values_cut_to_63_chars": [c for x in p for c in x["cut"]],
        "by_owner": inventory([x["labels"] for x in p]),
        "example_labels": p[0]["labels"],
    }


def main(argv: list[str]) -> int:
    if argv[:1] == ["--plan"] and len(argv) == 3:
        print("\n".join(write_plan(Path(argv[1]), argv[2])))
        return 0
    if argv[:1] == ["--compare"] and len(argv) == 2:
        folder, expected = Path(argv[1]), plan()
        described = {}
        for p in expected:
            f = folder / f"{p['id']}.describe.json"
            try:
                d = json.loads(f.read_text())
                described[p["id"]] = d[0] if isinstance(d, list) else d
            except (OSError, ValueError):
                pass
        errors = compare(described, expected)
        for e in errors:
            print(f"::error title=Day 24 labels differ::{e}")
        if errors:
            return 1
        s = summarise(expected)
        owners = ", ".join(f"{o}: {v['buckets']} bucket(s), {v['retention_days']} days"
                           for o, v in s["by_owner"].items())
        print(f"::notice title=Day 24 labelled at creation::{s['buckets']} store drop buckets created with "
              f"--labels-file and matched local: {s['labels_per_bucket']} labels each (plus the run label), "
              f"lifecycle delete ages as planned, 0 label-update calls; cut to 63 chars: "
              f"{s['values_cut_to_63_chars']}; by owner: {owners}")
        return 0
    if argv:
        print(__doc__)
        return 2
    s = summarise(plan())
    s = {"engine": "Python label rules (local) / gcloud storage buckets create --labels-file (cloud)", **s}
    (HERE / "result.json").write_text(json.dumps(s, indent=2) + "\n")
    print(json.dumps(s, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
