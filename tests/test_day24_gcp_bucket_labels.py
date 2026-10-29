"""Day 24 Google Cloud: free-text store attributes become valid Cloud Storage labels, the compare step accepts
gcloud's describe JSON and catches a missing label or lifecycle rule, and cloud.sh creates every bucket with
--labels-file in one call (run against a fake gcloud, no credentials)."""
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-24-labelled-drop-buckets"
spec = importlib.util.spec_from_file_location("drop_buckets", FEATURE / "drop_buckets.py")
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)


def test_label_values_follow_cloud_storage_rules():
    assert db.label_value("Ops – East") == ("ops-east", False)
    assert db.label_value("East Side #12") == ("east-side-12", False)
    assert db.label_value("Café Crème") == ("cafe-creme", False)
    v, cut = db.label_value("x" * 70)
    assert cut and len(v) == 63
    for p in db.plan("40755895619"):
        for k, val in p["labels"].items():
            assert db.KEY.match(k) and db.VALUE.match(val), (k, val)


def test_plan_labels_owner_cost_center_and_retention():
    p = {x["id"]: x for x in db.plan()}
    assert p["east"]["labels"] == {
        "store": "east", "store_name": "east-side-12", "region": "us-central", "owner": "ops-east",
        "feed": "pos-exports", "cost_center": "cc-4410", "retention_days": "30",
        "program": "cloud-microfeatures", "feature": "day-24"}
    assert p["hq"]["cut"] == ["hq.store_name"] and len(p["hq"]["labels"]["store_name"]) <= 63
    assert p["hq"]["lifecycle"] == {"rule": [{"action": {"type": "Delete"}, "condition": {"age": 90}}]}
    inv = db.inventory([x["labels"] for x in p.values()])
    assert inv["ops-east"] == {"buckets": 2, "cost_centers": ["cc-4410", "cc-4420"], "retention_days": [30]}


def test_compare_reads_describe_json_and_catches_differences():
    plan = db.plan("123")
    described = {p["id"]: {"name": "b", "labels": p["labels"], "lifecycle_config": p["lifecycle"]} for p in plan}
    assert db.compare(described, db.plan()) == []
    described["west"] = {**described["west"], "labels": {k: v for k, v in plan[1]["labels"].items() if k != "owner"}}
    described["hq"] = {**described["hq"], "lifecycle_config": {}}
    errors = db.compare(described, db.plan())
    assert len(errors) == 2 and "missing ['owner']" in errors[0] and "lifecycle" in errors[1]
    assert db.compare({}, db.plan())[0] == "east: no describe output"


def test_result_json_is_current():
    result = json.loads((FEATURE / "result.json").read_text())
    s = db.summarise(db.plan())
    assert {k: result[k] for k in s} == s


FAKE_GCLOUD = r"""#!/usr/bin/env bash
# fake gcloud: records calls, keeps bucket labels/lifecycle in $FAKE_STATE
echo "$*" >> "$FAKE_STATE/calls"
case "$1 $2 $3" in
  "help storage buckets") echo "  --labels-file=LABELS_FILE";;
  "storage ls gs://cmf-d24-*") exit 1;;
  "storage buckets create")
    b=${4#gs://}; mkdir -p "$FAKE_STATE/$b"
    for a in "$@"; do case "$a" in --labels-file=*) cp "${a#*=}" "$FAKE_STATE/$b/labels";;
      --lifecycle-file=*) cp "${a#*=}" "$FAKE_STATE/$b/life";; esac; done;;
  "storage buckets describe")
    b=${4#gs://}
    python -c "import json,sys; print(json.dumps({'name': sys.argv[1], 'labels': json.load(open(sys.argv[2])),
      'lifecycle_config': json.load(open(sys.argv[3]))}))" "$b" "$FAKE_STATE/$b/labels" "$FAKE_STATE/$b/life";;
  "storage rm -r") rm -rf "$FAKE_STATE/${4#gs://}";;
  *) echo "unexpected: $*" >&2; exit 2;;
esac
"""


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_cloud_sh_creates_labelled_buckets_in_one_call_and_cleans_up(tmp_path):
    bin_dir, state = tmp_path / "bin", tmp_path / "state"
    bin_dir.mkdir(), state.mkdir()
    (bin_dir / "gcloud").write_text(FAKE_GCLOUD)
    (bin_dir / "gcloud").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_STATE": str(state),
           "GCP_PROJECT_ID": "demo-project", "GITHUB_RUN_ID": "77"}
    r = subprocess.run(["bash", "cloud.sh"], cwd=FEATURE, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "::notice title=Day 24 labelled at creation::3 store drop buckets" in r.stdout
    calls = (state / "calls").read_text().splitlines()
    creates = [c for c in calls if c.startswith("storage buckets create")]
    assert len(creates) == 3 and all("--labels-file=" in c and "--lifecycle-file=" in c for c in creates)
    assert not any("buckets update" in c for c in calls)
    assert sum(c.startswith("storage rm -r") for c in calls) == 3
    assert [p.name for p in state.iterdir() if p.is_dir()] == []
