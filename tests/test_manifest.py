import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import manifest_tools as mt  # noqa: E402


def test_repo_manifest_is_valid():
    assert mt.validate(mt.load()) == []


def test_readme_tables_match_manifest():
    text = mt.README.read_text()
    data = mt.load()
    for track in mt.TRACKS:
        start_m, end_m = mt.MARKERS[track]
        start = text.index(start_m) + len(start_m)
        assert text[start:text.index(end_m)].strip() == mt.render_table(data, track).strip()


def _feature(day=1, platform="databricks", released="2026-09-01", built="2026-10-05", slug=None):
    slug = slug or f"day-{day:02d}-example"
    return {
        "day": day, "slug": slug, "date": built, "platform": platform,
        "title": "Example", "summary": "x", "skills": ["Delta Lake"], "status": "built",
        "path": f"features/{platform}/{slug}",
        "new_feature": {"name": "Something new", "released": released, "source": "https://example.com/notes"},
    }


def _errors(*features):
    data = copy.deepcopy(mt.load())
    data["features"] = list(features)
    return mt.validate(data)


def test_both_tracks_can_share_a_day():
    errs = _errors(_feature(platform="databricks"), _feature(platform="gcp"))
    assert not any("duplicate" in e for e in errs)


def test_same_track_cannot_repeat_a_day():
    assert any("duplicate databricks day 1" in e for e in _errors(_feature(), _feature()))


def test_days_are_consecutive_per_track():
    assert any("gcp days must be consecutive" in e for e in _errors(_feature(platform="gcp"), _feature(day=3, platform="gcp")))


def test_path_must_sit_under_its_track():
    f = _feature()
    f["path"] = "features/gcp/day-01-example"
    assert any("path must be features/databricks/" in e for e in _errors(f))


def test_old_platform_features_are_rejected():
    assert any("pick something newer" in e for e in _errors(_feature(released="2025-01-01")))


def test_new_feature_needs_a_source():
    f = _feature()
    del f["new_feature"]["source"]
    assert any("new_feature needs" in e for e in _errors(f))


def test_feature_folder_needs_to_exist():
    assert any("does not exist" in e for e in _errors(_feature()))


def test_next_days_counts_each_track():
    data = {"features": [_feature(), _feature(day=2), _feature(platform="gcp")]}
    assert mt.next_days(data) == {"databricks": 3, "gcp": 2}
