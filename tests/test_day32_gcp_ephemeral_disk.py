"""Day 32 Google Cloud: the external merge sort (runs spilled to a directory, k-way merge) equals the no-sort
reference; result.json is current; the job entry point prints one DAY32_RESULT line and leaves the disk empty; and the
gcloud orchestration creates the job with an ephemeral-disk volume, reads the result from Cloud Logging, compares,
and always deletes the job (fake gcloud, no credentials)."""
import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-32-scan-sort-ephemeral-disk"
sys.path.insert(0, str(FEATURE))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ss = _load("spill_sort")
cr = _load("cloud_run")
RESULT = json.loads((FEATURE / "result.json").read_text())
KEYS = ("rows", "runs", "spilled_bytes", "merged_bytes", "distinct_skus", "units", "revenue", "top_skus", "checksum")


def test_result_json_is_current():
    ref = ss.reference()
    assert {k: RESULT[k] for k in KEYS} == json.loads(json.dumps(ref))
    assert (RESULT["rows_per_run"], RESULT["disk_size"]) == (ss.RUN_SIZE, ss.DISK_SIZE)


def test_headline_numbers():
    assert (RESULT["rows"], RESULT["runs"], RESULT["distinct_skus"]) == (6_000_000, 6, 400)
    assert RESULT["spilled_bytes"] == RESULT["merged_bytes"] == 97_659_429
    assert RESULT["top_skus"][0] == {"sku": "P0000", "units": 750731, "revenue": 743223.69}


def test_external_sort_equals_reference(tmp_path):
    out = ss.external_sort(50_000, str(tmp_path), run_size=7_000)
    assert out == ss.reference(50_000, run_size=7_000)
    assert out["runs"] == 8 and list(tmp_path.iterdir()) == []      # run files removed after the merge


def test_job_entry_point_prints_one_result_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ss, "ROWS", 20_000)
    monkeypatch.setattr(ss, "RUN_SIZE", 3_000)
    monkeypatch.setenv("DAY32_DISK", str(tmp_path))
    assert ss.job_main() == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("DAY32_RESULT ")]
    assert len(lines) == 1
    res = json.loads(lines[0][len("DAY32_RESULT "):])
    assert {k: res[k] for k in KEYS} == json.loads(json.dumps(ss.reference(20_000, 3_000)))
    assert res["disk"]["left_after"] == 0 and res["disk"]["size_gib"] > 0


def test_create_args_mount_an_ephemeral_disk():
    args = cr.create_args("us-central1", "/tmp/env.yaml")
    assert args[:4] == ["run", "jobs", "create", ss.JOB]
    assert "--add-volume=name=spill,type=ephemeral-disk,size=1Gi" in args
    assert "--add-volume-mount=volume=spill,mount-path=/mnt/spill" in args
    assert "--memory=512Mi" in args and "--max-retries=0" in args
    assert "," not in cr.COMMAND_ARG                       # gcloud splits --args on commas
    assert cr.create_args("r", "f", beta=True)[0] == "beta"


def test_env_file_carries_the_code(tmp_path):
    text = cr.env_file_text(b"print('hi')\n")
    assert "DAY32_MODE: job" in text and f'"{base64.b64encode(b"print(\'hi\')\n").decode()}"' in text


def _cloud_copy(**disk):
    return {**{k: RESULT[k] for k in KEYS}, "seconds": 41.2, "peak_rss_mib": 90,
            "disk": {"mount": "/mnt/spill", "fs_type": "ext4", "size_gib": 0.98, "memory_limit_mib": 512,
                     "left_after": 0, **disk}}


def test_compare_checks_numbers_and_the_disk():
    assert cr.compare(_cloud_copy(), RESULT) == []
    bad = _cloud_copy(fs_type="tmpfs", left_after=2)
    bad["checksum"] = "0" * 16
    errors = cr.compare(bad, RESULT)
    assert len(errors) == 3 and errors[0].startswith("checksum") and "ext4" in errors[1] and "left" in errors[2]


class FakeGcloud:
    def __init__(self, result=None, create_error=None, logs_after=1, leftovers=()):
        self.calls, self.result, self.create_error, self.logs_after = [], result, create_error, logs_after
        self.jobs, self.reads = set(leftovers), 0

    def __call__(self, args, timeout=900):
        self.calls.append(args)
        a = [x for x in args if x != "beta"]
        if a[:2] == ["run", "jobs"] and a[2] == "list":
            return "\n".join(sorted(self.jobs))
        if a[:3] == ["run", "jobs", "create"]:
            if self.create_error and not any(x.startswith("--service-account") for x in a):
                raise cr.GcloudError(self.create_error)
            self.jobs.add(a[3])
            self.created = a
            return ""
        if a[:3] == ["run", "jobs", "delete"]:
            self.jobs.discard(a[3])
            return ""
        if a[:3] == ["run", "jobs", "execute"]:
            return "cmf-d32-ephemeral-sort-abc12\n"
        if a[:2] == ["config", "get-value"]:
            return "deployer@example-project.iam.gserviceaccount.com\n"
        if a[:2] == ["logging", "read"]:
            self.reads += 1
            entries = [{"textPayload": "starting"}]
            if self.result is not None and self.reads >= self.logs_after:
                entries.insert(0, {"textPayload": "DAY32_RESULT " + json.dumps(self.result)})
            return json.dumps(entries)
        return ""


def _run(fake, monkeypatch):
    monkeypatch.setenv("GCP_REGION", "us-central1")
    return cr.run(FEATURE / "spill_sort.py", gcloud=fake, sleep=lambda s: None)


def test_cloud_run_end_to_end(monkeypatch, capsys):
    fake = FakeGcloud(_cloud_copy(), logs_after=3, leftovers={"cmf-d32-ephemeral-sort"})
    assert _run(fake, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "matched local" in out and "6,000,000 scan lines" in out and "ext4 ephemeral disk" in out
    assert fake.jobs == set() and fake.reads == 3
    reads = [c for c in fake.calls if c[:2] == ["logging", "read"]]
    assert 'execution_name"="cmf-d32-ephemeral-sort-abc12"' in reads[0][2]
    assert any("type=ephemeral-disk" in x for x in fake.created)


def test_falls_back_to_signed_in_account(monkeypatch, capsys):
    fake = FakeGcloud(_cloud_copy(), create_error="PERMISSION_DENIED: iam.serviceAccounts.actAs on the default "
                                                  "compute service account")
    assert _run(fake, monkeypatch) == 0
    assert "signed-in service account" in capsys.readouterr().out
    assert any(x.startswith("--service-account=") for x in fake.created) and fake.jobs == set()


def test_missing_result_line_is_an_error_and_job_deleted(monkeypatch, capsys):
    monkeypatch.setattr(cr, "LOG_WAIT_SECONDS", 0)
    fake = FakeGcloud(None)
    assert _run(fake, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "no DAY32_RESULT line" in out and "starting" in out and fake.jobs == set()


def test_create_failure_is_an_error(monkeypatch, capsys):
    fake = FakeGcloud(None, create_error="INVALID_ARGUMENT: quota for ephemeral disk is 0")
    fake_calls = fake.__call__

    def always_fail(args, timeout=900):
        if args[:1] == ["config"]:
            raise AssertionError("no fallback expected")
        return fake_calls(args, timeout)
    assert _run(always_fail, monkeypatch) == 1
    assert "quota for ephemeral disk" in capsys.readouterr().out
