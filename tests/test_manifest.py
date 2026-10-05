import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import manifest_tools as mt  # noqa: E402


def test_repo_manifest_is_valid():
    assert mt.validate(mt.load()) == []


def test_readme_table_matches_manifest():
    text = mt.README.read_text()
    start = text.index(mt.TABLE_START) + len(mt.TABLE_START)
    assert text[start:text.index(mt.TABLE_END)].strip() == mt.render_table(mt.load()).strip()


def _feature(day=1, platform="databricks", path="features"):
    return {
        "day": day, "slug": f"day-{day:02d}-example", "date": "2026-10-05", "platform": platform,
        "title": "Example", "summary": "x", "skills": ["Delta Lake"], "status": "built", "path": path,
    }


def test_platform_must_match_phase(tmp_path, monkeypatch):
    data = copy.deepcopy(mt.load())
    data["features"] = [_feature(day=1, platform="gcp")]
    assert any("belongs to the databricks phase" in e for e in mt.validate(data))


def test_days_must_be_consecutive():
    data = copy.deepcopy(mt.load())
    data["features"] = [_feature(day=1), _feature(day=3)]
    assert any("consecutive" in e for e in mt.validate(data))


def test_feature_folder_needs_readme():
    data = copy.deepcopy(mt.load())
    data["features"] = [_feature(path="scripts")]  # exists, but has no README.md
    assert any("README.md is required" in e for e in mt.validate(data))
