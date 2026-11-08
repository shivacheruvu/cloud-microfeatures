"""Day 34 Google Cloud: the Python 3.12 reference equals result.json; job315.py really uses the Python 3.15 features
(and, where a python3.15 is installed, prints the same result); the gcloud orchestration creates the job on the
python315 runtime image, falls back to the next interpreter name, compares, and always deletes the job (fake gcloud,
no credentials)."""
import base64
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-34-shelf-price-py315"
sys.path.insert(0, str(FEATURE))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ru = _load("rollup")
cr = _load("cloud_run")
RESULT = json.loads((FEATURE / "result.json").read_text(encoding="utf-8"))
JOB_SRC = (FEATURE / "job315.py").read_text(encoding="utf-8")


def test_result_json_is_current():
    assert {k: RESULT[k] for k in ru.KEYS} == json.loads(json.dumps(ru.reference()))
    assert RESULT["image"].endswith("/runtimes/python315")


def test_headline_numbers():
    assert (RESULT["stores"], RESULT["baskets"], RESULT["lines"], RESULT["units"]) == (4, 10, 27, 48)
    assert (RESULT["gross_cents"], RESULT["discount_cents"], RESULT["net_cents"]) == (16906, 1718, 15188)
    assert (RESULT["promo_rules_raw"], RESULT["promo_rules_distinct"]) == (8, 6)
    assert RESULT["median_basket_cents"] == 1434.5


def test_layers_override_in_order():
    p = RESULT["shelf_prices"]
    assert p["S1"]["P01"] == 329 and p["S1"]["P04"] == 449       # region north, then store S1
    assert p["S3"]["P01"] == 299 and p["S3"]["P02"] == 399       # store S3 beats chain; region south
    assert p["S2"]["P04"] == 499 and p["S4"]["P06"] == 799


def test_duplicate_rules_with_other_key_order_collapse():
    raw, rules = ru.distinct_rules(ru.load())
    assert len(raw) == 8 and len(rules) == 6
    assert sorted(r["sku"] for r in rules) == ["P01", "P02", "P04", "P06", "P07", "P08"]


def test_job_uses_python_315_features():
    assert "{**layers[name] for name in stack}" in JOB_SRC          # PEP 798 dict unpacking in a comprehension
    assert "[*feed for feed in" in JOB_SRC and '[*b["lines"] for b in' in JOB_SRC   # PEP 798 list unpacking
    assert "frozendict(r)" in JOB_SRC                                # PEP 814 built-in frozendict
    assert "\nlazy import statistics\n" in JOB_SRC                   # PEP 810 explicit lazy import
    if sys.version_info < (3, 15):
        with pytest.raises(SyntaxError):
            compile(JOB_SRC, "job315.py", "exec")


@pytest.mark.skipif(shutil.which("python3.15") is None, reason="no python3.15 installed")
def test_job_under_python_315_matches_reference(tmp_path):
    env = {"DAY34_MODE": "job", "DAY34_CODE": base64.b64encode(JOB_SRC.encode()).decode(),
           "DAY34_DATA": base64.b64encode((FEATURE / "data.json").read_bytes()).decode(), "PATH": "/usr/bin:/bin"}
    out = subprocess.run([shutil.which("python3.15"), "-c", cr.COMMAND_ARG], env=env, capture_output=True,
                         text=True, cwd=tmp_path, check=True).stdout
    cloud = json.loads(out.split("DAY34_RESULT ", 1)[1])
    assert cr.compare(cloud, RESULT) == []


def _runtime(**kw):
    rt = {"python": "3.15.0", "release_level": "final", "utf8_mode": 1, "lazy_import_deferred": True,
          "receipt": {"bytes": 135, "utf8": True}, "frozendict_builtin": True}
    rt.update(kw)
    return rt


def test_compare_flags_wrong_runtime():
    good = {**{k: RESULT[k] for k in ru.KEYS}, "runtime": _runtime()}
    assert cr.compare(good, RESULT) == []
    assert "expected 3.15" in cr.compare({**good, "runtime": _runtime(python="3.14.1")}, RESULT)[0]
    assert "UTF-8" in cr.compare({**good, "runtime": _runtime(utf8_mode=0)}, RESULT)[0]
    assert "net_cents" in cr.compare({**good, "net_cents": 1}, RESULT)[0]


def test_create_args_use_the_runtime_image():
    args = cr.create_args("us-central1", "/tmp/env.yaml")
    assert args[:4] == ["run", "jobs", "create", ru.JOB]
    assert f"--image={ru.IMAGE}" in args and "--command=python3" in args
    assert "--max-retries=0" in args and "--memory=512Mi" in args
    assert "," not in cr.COMMAND_ARG                       # gcloud splits --args on commas


def test_env_file_carries_code_and_data():
    text = cr.env_file_text(b"print(1)", b"{}")
    assert "DAY34_MODE: job" in text
    assert base64.b64encode(b"print(1)").decode() in text and base64.b64encode(b"{}").decode() in text


class FakeGcloud:
    def __init__(self, result, fail_first_execute=False):
        self.calls, self.result, self.fail_first = [], result, fail_first_execute
        self.executes = 0

    def __call__(self, args, timeout=900):
        self.calls.append(args)
        if args[:3] == ["run", "jobs", "execute"]:
            self.executes += 1
            if self.fail_first and self.executes == 1:
                raise cr.GcloudError("gcloud run jobs execute: task failed: exec: python3: not found")
            return "cmf-d34-py315-shelf-abc12\n"
        if args[:2] == ["logging", "read"]:
            return json.dumps([{"textPayload": "DAY34_RESULT " + json.dumps(self.result)}])
        if args[:3] == ["run", "jobs", "list"]:
            return ru.JOB + "\n"
        return ""


def test_run_matches_and_deletes(capsys):
    fake = FakeGcloud({**{k: RESULT[k] for k in ru.KEYS}, "runtime": _runtime()})
    assert cr.run(gcloud=fake, sleep=lambda s: None) == 0
    out = capsys.readouterr().out
    assert "matched local" in out and "::error" not in out
    deletes = [c for c in fake.calls if c[:3] == ["run", "jobs", "delete"]]
    assert len(deletes) == 2                               # leftover cleanup before, and after


def test_run_falls_back_to_next_interpreter(capsys):
    fake = FakeGcloud({**{k: RESULT[k] for k in ru.KEYS}, "runtime": _runtime()}, fail_first_execute=True)
    assert cr.run(gcloud=fake, sleep=lambda s: None) == 0
    assert any(c[:3] == ["run", "jobs", "update"] and "--command=python" in c for c in fake.calls)
    assert "--command=python)" in capsys.readouterr().out


def test_run_reports_mismatch_and_still_deletes(capsys):
    fake = FakeGcloud({**{k: RESULT[k] for k in ru.KEYS}, "net_cents": 0, "runtime": _runtime()})
    assert cr.run(gcloud=fake, sleep=lambda s: None) == 1
    assert "::error title=Google Cloud day 34 differs from local::net_cents" in capsys.readouterr().out
    assert any(c[:3] == ["run", "jobs", "delete"] for c in fake.calls[-3:])
