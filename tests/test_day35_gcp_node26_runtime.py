"""Day 35 Google Cloud: the Python zoneinfo reference equals result.json and gets the fall-back night right; job26.js
uses the Node.js 26 features, is ASCII (the job decodes it with atob), and its Intl.DateTimeFormat and Temporal
(--harmony-temporal) paths both give the reference delivery times on the local node; the gcloud orchestration
creates the job on the nodejs26 runtime image with the Temporal flag, falls back to no flag and then the next image
stack, compares, and always deletes the job (fake gcloud, no credentials)."""
import base64
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-35-delivery-slots-node26"
sys.path.insert(0, str(FEATURE))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sl = _load("slots")
cr = _load("cloud_run")
RESULT = json.loads((FEATURE / "result.json").read_text(encoding="utf-8"))
JOB_SRC = (FEATURE / "job26.js").read_text(encoding="utf-8")


def test_result_json_is_current():
    assert {k: RESULT[k] for k in sl.KEYS} == json.loads(json.dumps(sl.reference()))
    assert RESULT["image"].endswith("/google-24/runtimes/nodejs26")


def test_fall_back_day_lengths():
    s = RESULT["stores"]
    assert [s[k]["hours_in_day"] for k in ("S1", "S2", "S3", "S4")] == [25, 25, 24, 25]   # Phoenix has no DST
    assert [s[k]["restock_hours"] for k in ("S1", "S2", "S3", "S4")] == [9, 9, 8, 9]


def test_delivery_promises():
    o = {x["id"]: x for x in RESULT["orders"]}
    assert o["O02"]["deliver_by"] == "2026-11-01T01:40-06:00"          # 00:40 CDT + 2 h = 01:40 CST
    assert o["O02"]["naive_deliver_by"] == "2026-11-01T02:40" and o["O02"]["late_if_naive"]
    assert o["O03"]["placed"] == "2026-11-01T01:30-05:00"              # ambiguous time takes the earlier offset
    assert o["O03"]["deliver_by"] == "2026-11-01T01:30-06:00"          # one hour later, same wall clock
    assert o["O07"]["late_if_naive"] is False and o["O04"]["late_if_naive"] is False
    assert o["O01"]["next_day_pickup"] == "2026-11-01T23:15-06:00" and o["O01"]["hours_to_pickup"] == 25
    assert (RESULT["late_if_naive"], RESULT["deliveries_in_repeated_hour"]) == (7, 5)


def test_precise_total_and_literal_names():
    assert RESULT["total_precise"] == 85.13 and RESULT["total_naive"] != RESULT["total_precise"]
    assert RESULT["name_matches"] == [["M&M's (Peanut)", 2, 1], ["Half & Half 1.5 qt", 1, 2],
                                      ["Dr. Pepper 2L", 2, 2], ["Kombucha [Ginger]", 2, 1]]
    assert RESULT["utf8_names"] == ["Jalapeño salsa 16oz", "Crème fraîche 8oz"]


def test_job_uses_node_26_features():
    for needle in ("Temporal.PlainDateTime.from(", ".hoursInDay", "z.add({ days: n })", "Math.sumPrecise(prices)",
                   "RegExp.escape(name)", "Uint8Array.fromBase64(raw)", "new Intl.DateTimeFormat("):
        assert needle in JOB_SRC
    assert JOB_SRC.isascii()
    assert "," not in cr.COMMAND_ARG                       # gcloud splits --args on commas


def _run_node(tmp_path, *flags):
    env = {"DAY35_CODE": base64.b64encode(JOB_SRC.encode()).decode(),
           "DAY35_DATA": base64.b64encode((FEATURE / "data.json").read_bytes()).decode()}
    p = subprocess.run([shutil.which("node"), *flags, "-e", cr.COMMAND_ARG], env=env, capture_output=True, text=True,
                       cwd=tmp_path)
    if p.returncode != 0 or "DAY35_RESULT " not in p.stdout:
        return None
    return json.loads(p.stdout.split("DAY35_RESULT ", 1)[1])


TIME_KEYS = ("orders", "stores", "late_if_naive", "deliveries_in_repeated_hour", "total_naive", "total_precise",
             "utf8_names")


@pytest.mark.skipif(shutil.which("node") is None, reason="no node installed")
def test_job_intl_path_matches_reference_on_local_node(tmp_path):
    cloud = _run_node(tmp_path)
    assert cloud is not None
    assert {k: cloud[k] for k in TIME_KEYS} == {k: RESULT[k] for k in TIME_KEYS}
    if not cloud["runtime"]["node"].startswith("26."):
        assert any("expected 26" in e for e in cr.compare(cloud, RESULT))


@pytest.mark.skipif(shutil.which("node") is None, reason="no node installed")
def test_job_temporal_path_matches_reference_where_the_flag_works(tmp_path):
    cloud = _run_node(tmp_path, cr.TEMPORAL_FLAG)
    if cloud is None or cloud["runtime"]["tz_engine"] != "Temporal":
        pytest.skip("this node has no usable Temporal behind --harmony-temporal")
    assert cloud["runtime"]["engines_agree"] is True
    assert {k: cloud[k] for k in TIME_KEYS} == {k: RESULT[k] for k in TIME_KEYS}


def _runtime(**kw):
    rt = {"node": "26.12.1", "v8": "14.6.1", "errors": {}, "ms": 12, "tz_engine": "Temporal", "engines_agree": True,
          "sum_engine": "integer cents", "temporal_with_harmony_flag": "object",
          "features": {"Temporal": "object", "Math.sumPrecise": "function", "RegExp.escape": "function",
                       "Uint8Array.fromBase64": "function"}}
    rt.update(kw)
    return rt


def _good(**kw):
    return {**{k: RESULT[k] for k in sl.KEYS}, "runtime": _runtime(), **kw}


def test_compare_flags_wrong_runtime():
    assert cr.compare(_good(), RESULT) == []
    assert "expected 26" in cr.compare(_good(runtime=_runtime(node="24.1.0")), RESULT)[0]
    feats = {**_runtime()["features"], "Math.sumPrecise": "undefined", "Temporal": "undefined"}
    assert cr.compare(_good(runtime=_runtime(features=feats)), RESULT) == []      # optional, reported only
    feats = {**_runtime()["features"], "RegExp.escape": "undefined"}
    assert "RegExp.escape" in cr.compare(_good(runtime=_runtime(features=feats)), RESULT)[0]
    assert "different" in cr.compare(_good(runtime=_runtime(engines_agree=False)), RESULT)[0]
    assert "threw" in cr.compare(_good(runtime=_runtime(errors={"Temporal orders": "x"})), RESULT)[0]
    assert "late_if_naive" in cr.compare(_good(late_if_naive=0), RESULT)[0]


def test_create_args_use_the_runtime_image():
    args = cr.create_args("us-central1", "/tmp/env.yaml")
    assert args[:4] == ["run", "jobs", "create", sl.JOB]
    assert f"--image={sl.IMAGES[0]}" in args and "--command=node" in args
    assert f"--args=--harmony-temporal,-e,{cr.COMMAND_ARG}" in args
    assert cr.node_args(()) == f"--args=-e,{cr.COMMAND_ARG}"
    assert "--max-retries=0" in args and "--memory=512Mi" in args


def test_env_file_carries_code_and_data():
    text = cr.env_file_text(b"console.log(1)", b"{}")
    assert base64.b64encode(b"console.log(1)").decode() in text and base64.b64encode(b"{}").decode() in text


class FakeGcloud:
    def __init__(self, result, fail_executes=0):
        self.calls, self.result, self.fail = [], result, fail_executes
        self.executes = 0

    def __call__(self, args, timeout=900):
        self.calls.append(args)
        if args[:3] == ["run", "jobs", "execute"]:
            self.executes += 1
            if self.executes <= self.fail:
                raise cr.GcloudError("gcloud run jobs execute: task failed: exec: node: not found")
            return "cmf-d35-node26-slots-abc12\n"
        if args[:2] == ["logging", "read"]:
            return json.dumps([{"textPayload": "DAY35_RESULT " + json.dumps(self.result)}])
        if args[:3] == ["run", "jobs", "list"]:
            return sl.JOB + "\n"
        return ""


def test_run_matches_and_deletes(capsys):
    fake = FakeGcloud(_good())
    assert cr.run(gcloud=fake, sleep=lambda s: None) == 0
    out = capsys.readouterr().out
    assert "matched local" in out and "::error" not in out and "25 h in Chicago" in out
    assert len([c for c in fake.calls if c[:3] == ["run", "jobs", "delete"]]) == 2   # leftover cleanup, and after


def test_run_falls_back_to_no_flag_then_next_image(capsys):
    fake = FakeGcloud(_good(), fail_executes=1)
    assert cr.run(gcloud=fake, sleep=lambda s: None) == 0
    updates = [c for c in fake.calls if c[:3] == ["run", "jobs", "update"]]
    assert updates[-1][-1] == f"--args=-e,{cr.COMMAND_ARG}" and f"--image={sl.IMAGES[0]}" in updates[-1]
    fake = FakeGcloud(_good(), fail_executes=2)
    assert cr.run(gcloud=fake, sleep=lambda s: None) == 0
    updates = [c for c in fake.calls if c[:3] == ["run", "jobs", "update"]]
    assert f"--image={sl.IMAGES[1]}" in updates[-1] and updates[-1][-1].startswith("--args=--harmony-temporal")
    assert "google-24-full" in capsys.readouterr().out


def test_run_reports_mismatch_and_still_deletes(capsys):
    fake = FakeGcloud(_good(total_precise=0))
    assert cr.run(gcloud=fake, sleep=lambda s: None) == 1
    assert "::error title=Google Cloud day 35 differs from local::total_precise" in capsys.readouterr().out
    assert any(c[:3] == ["run", "jobs", "delete"] for c in fake.calls[-3:])
