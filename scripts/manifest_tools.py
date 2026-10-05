"""Validate manifest.json and render the README feature tables.

Two tracks ship every day: one Databricks microfeature and one Google Cloud microfeature.
Each must use a platform feature released recently (see program.max_feature_age_days).

Usage:
  python scripts/manifest_tools.py validate
  python scripts/manifest_tools.py render      # rewrites the tables in README.md
  python scripts/manifest_tools.py next        # prints the next day number for each track
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifest.json"
README = ROOT / "README.md"

TRACKS = ("databricks", "gcp")
STATUSES = {"built", "ran", "ran-local", "blocked"}
REQUIRED = {"day", "slug", "date", "platform", "title", "summary", "skills", "status", "path", "new_feature"}
SLUG = re.compile(r"^day-\d{2}-[a-z0-9-]+$")
MARKERS = {t: (f"<!-- {t}:start -->", f"<!-- {t}:end -->") for t in TRACKS}


def load() -> dict:
    return json.loads(MANIFEST.read_text())


def _check_new_feature(nf, built_on: date | None, max_age: int, where: str) -> list[str]:
    if not isinstance(nf, dict) or not {"name", "released", "source"} <= nf.keys():
        return [f"{where}: new_feature needs name, released (YYYY-MM-DD) and source (release-notes URL)"]
    errors = []
    if not str(nf["source"]).startswith("https://"):
        errors.append(f"{where}: new_feature.source must be an https release-notes link")
    try:
        released = date.fromisoformat(nf["released"])
    except (TypeError, ValueError):
        return errors + [f"{where}: new_feature.released must be YYYY-MM-DD"]
    if built_on:
        age = (built_on - released).days
        if age < 0:
            errors.append(f"{where}: new_feature.released is after the build date")
        elif age > max_age:
            errors.append(f"{where}: {nf['name']} was released {age} days before the build; pick something newer than {max_age} days")
    return errors


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    program, features = data["program"], data["features"]
    max_age = program.get("max_feature_age_days", 180)
    days_by_track: dict[str, list[int]] = {t: [] for t in TRACKS}
    for i, f in enumerate(features):
        where = f"features[{i}]"
        missing = REQUIRED - f.keys()
        if missing:
            errors.append(f"{where}: missing {sorted(missing)}")
            continue
        day, platform = f["day"], f["platform"]
        if platform not in TRACKS:
            errors.append(f"{where}: platform must be one of {list(TRACKS)}")
            continue
        if not isinstance(day, int) or not 1 <= day <= program["total_days"]:
            errors.append(f"{where}: day must be 1..{program['total_days']}")
            continue
        if day in days_by_track[platform]:
            errors.append(f"{where}: duplicate {platform} day {day}")
        days_by_track[platform].append(day)
        if f["status"] not in STATUSES:
            errors.append(f"{where}: status must be one of {sorted(STATUSES)}")
        if not SLUG.match(f["slug"]) or not f["slug"].startswith(f"day-{day:02d}-"):
            errors.append(f"{where}: slug must look like day-{day:02d}-short-name")
        if f["path"] != f"features/{platform}/{f['slug']}":
            errors.append(f"{where}: path must be features/{platform}/{f['slug']}")
        try:
            built_on = date.fromisoformat(f["date"])
        except (TypeError, ValueError):
            built_on = None
            errors.append(f"{where}: date must be YYYY-MM-DD")
        errors += _check_new_feature(f["new_feature"], built_on, max_age, where)
        folder = ROOT / f["path"]
        if not folder.is_dir():
            errors.append(f"{where}: folder {f['path']} does not exist")
        elif not (folder / "README.md").is_file():
            errors.append(f"{where}: {f['path']}/README.md is required")
        if not isinstance(f["skills"], list) or not f["skills"]:
            errors.append(f"{where}: skills must be a non-empty list")
    for track, days in days_by_track.items():
        days = sorted(set(days))
        if days and days != list(range(1, len(days) + 1)):
            errors.append(f"{track} days must be consecutive from 1, got {days}")
    return errors


def next_days(data: dict) -> dict[str, int]:
    out = {}
    for t in TRACKS:
        days = [f["day"] for f in data["features"] if f.get("platform") == t]
        out[t] = max(days, default=0) + 1
    return out


def render_table(data: dict, track: str) -> str:
    rows = ["| Day | Microfeature | New platform feature | Shows | Status |", "|---|---|---|---|---|"]
    for f in sorted((x for x in data["features"] if x["platform"] == track), key=lambda x: x["day"]):
        nf = f["new_feature"]
        rows.append(
            f"| {f['day']} | [{f['title']}]({f['path']}) | [{nf['name']}]({nf['source']}) ({nf['released']}) "
            f"| {', '.join(f['skills'])} | {f['status']} |"
        )
    if len(rows) == 2:
        rows.append("| — | First microfeature ships on day 1 | — | — | — |")
    return "\n".join(rows)


def render(data: dict) -> None:
    text = README.read_text()
    for t in TRACKS:
        start_m, end_m = MARKERS[t]
        start, end = text.index(start_m) + len(start_m), text.index(end_m)
        text = text[:start] + "\n" + render_table(data, t) + "\n" + text[end:]
    README.write_text(text)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "validate"
    data = load()
    if cmd == "next":
        nd = next_days(data)
        print(" ".join(f"{t}={d}" for t, d in nd.items()))
        sys.exit(0)
    problems = validate(data)
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    if cmd == "render":
        render(data)
        print("README tables updated.")
    else:
        print(f"manifest ok: {len(data['features'])} feature(s)")
