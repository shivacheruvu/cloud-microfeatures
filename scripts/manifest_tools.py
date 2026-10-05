"""Validate manifest.json and render the README feature table.

Usage:
  python scripts/manifest_tools.py validate
  python scripts/manifest_tools.py render      # rewrites the table in README.md
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

PLATFORMS = {"databricks", "gcp"}
STATUSES = {"built", "ran", "ran-local", "blocked"}
REQUIRED = {"day", "slug", "date", "platform", "title", "summary", "skills", "status", "path"}
SLUG = re.compile(r"^day-\d{2}-[a-z0-9-]+$")
TABLE_START, TABLE_END = "<!-- features:start -->", "<!-- features:end -->"


def load() -> dict:
    return json.loads(MANIFEST.read_text())


def phase_for(day: int, program: dict) -> str | None:
    for p in program["phases"]:
        lo, hi = p["days"]
        if lo <= day <= hi:
            return p["platform"]
    return None


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    program, features = data["program"], data["features"]
    seen_days: set[int] = set()
    for i, f in enumerate(features):
        where = f"features[{i}]"
        missing = REQUIRED - f.keys()
        if missing:
            errors.append(f"{where}: missing {sorted(missing)}")
            continue
        day = f["day"]
        if not isinstance(day, int) or not 1 <= day <= program["total_days"]:
            errors.append(f"{where}: day must be 1..{program['total_days']}")
        if day in seen_days:
            errors.append(f"{where}: duplicate day {day}")
        seen_days.add(day)
        if f["platform"] not in PLATFORMS:
            errors.append(f"{where}: platform must be one of {sorted(PLATFORMS)}")
        elif phase_for(day, program) != f["platform"]:
            errors.append(f"{where}: day {day} belongs to the {phase_for(day, program)} phase")
        if f["status"] not in STATUSES:
            errors.append(f"{where}: status must be one of {sorted(STATUSES)}")
        if not SLUG.match(f["slug"]) or not f["slug"].startswith(f"day-{day:02d}-"):
            errors.append(f"{where}: slug must look like day-{day:02d}-short-name")
        try:
            date.fromisoformat(f["date"])
        except ValueError:
            errors.append(f"{where}: date must be YYYY-MM-DD")
        if not (ROOT / f["path"]).is_dir():
            errors.append(f"{where}: folder {f['path']} does not exist")
        elif not (ROOT / f["path"] / "README.md").is_file():
            errors.append(f"{where}: {f['path']}/README.md is required")
        if not isinstance(f["skills"], list) or not f["skills"]:
            errors.append(f"{where}: skills must be a non-empty list")
    days = sorted(seen_days)
    if days and days != list(range(1, len(days) + 1)):
        errors.append(f"days must be consecutive from 1, got {days}")
    return errors


def render_table(data: dict) -> str:
    rows = ["| Day | Platform | Microfeature | Shows | Status |", "|---|---|---|---|---|"]
    for f in sorted(data["features"], key=lambda x: x["day"]):
        platform = "Databricks" if f["platform"] == "databricks" else "Google Cloud"
        rows.append(
            f"| {f['day']} | {platform} | [{f['title']}]({f['path']}) | {', '.join(f['skills'])} | {f['status']} |"
        )
    if len(rows) == 2:
        rows.append("| — | — | First microfeature ships on day 1 | — | — |")
    return "\n".join(rows)


def render(data: dict) -> None:
    text = README.read_text()
    start, end = text.index(TABLE_START) + len(TABLE_START), text.index(TABLE_END)
    README.write_text(text[:start] + "\n" + render_table(data) + "\n" + text[end:])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "validate"
    data = load()
    problems = validate(data)
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    if cmd == "render":
        render(data)
        print("README table updated.")
    else:
        print(f"manifest ok: {len(data['features'])} feature(s)")
