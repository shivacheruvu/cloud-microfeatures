"""Day 37 Databricks: the stdlib model of job completion triggers follows the documented rules (if_status filters,
AND across jobs, latest completion wins, nothing from before the trigger, reset after firing), the restock plan and
result.json are right, the job settings carry the trigger and the completed_jobs parameter, and the cloud check
accepts the expected completions and rejects others (no cloud)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-37-restock-after-receiving"
sys.path.insert(0, str(FEATURE))
spec = importlib.util.spec_from_file_location("completion_trigger", FEATURE / "completion_trigger.py")
ct = importlib.util.module_from_spec(spec)
sys.modules["completion_trigger"] = ct
spec.loader.exec_module(ct)
spec2 = importlib.util.spec_from_file_location("run_cloud_day37", FEATURE / "run_cloud.py")
rc = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(rc)


def test_if_status_filters():
    assert ct.matches("SUCCESS", "SUCCESS") and ct.matches("SUCCESS", "SUCCESS_WITH_FAILURES")
    assert not ct.matches("SUCCESS", "FAILED") and ct.matches("FAILED", "TIMEDOUT")
    assert not ct.matches("FAILED", "SUCCESS_WITH_FAILURES") and ct.matches("DONE", "CANCELED")
    with pytest.raises(ValueError):
        ct.matches("ANY", "SUCCESS")


def test_and_latest_wins_and_reset():
    runs = ct.replay("SUCCESS")
    assert [[c["run_id"] for c in r["completed_jobs"]] for r in runs] == [["9102", "9202"], ["9103", "9203"]]
    assert [r["started_after"] for r in runs] == ["2026-11-10T07:30", "2026-11-10T13:00"]
    first = runs[0]["completed_jobs"][0]
    assert set(first) == {"workspace_id", "job_id", "run_id", "termination_code", "completion_time"}
    assert [c["job_id"] for c in runs[0]["completed_jobs"]] == [ct.DC_JOB, ct.DSD_JOB]   # configuration order
    assert [[c["run_id"] for c in r["completed_jobs"]] for r in ct.replay("DONE")] == [["9102", "9201"], ["9103", "9203"]]
    assert ct.replay("FAILED") == []


def test_completions_before_the_trigger_are_ignored():
    t = ct.Trigger([ct.DC_JOB, ct.DSD_JOB])
    assert t.completion("2026-11-10T05:00", ct.DSD_JOB, "1", "SUCCESS") is None
    assert t.completion("2026-11-10T06:00", ct.DC_JOB, "2", "SUCCESS") is None   # the DSD run predates the trigger
    assert t.completion("2026-11-10T06:01", "999", "3", "SUCCESS") is None       # not a monitored job


def test_trigger_limits():
    with pytest.raises(ValueError):
        ct.Trigger([str(i) for i in range(11)])
    with pytest.raises(ValueError):
        ct.Trigger(["1", "1"])


def test_restock_plan_and_result_json():
    plan = ct.restock_plan(ct.replay()[0]["completed_jobs"])
    assert plan["oat milk 64 oz"] == {"received": 40, "to_shelf": 20, "to_backroom": 20}   # 9102, not 9101
    assert ct.task_main("[]") == {"manual_run": True, "plan": {}}
    assert ct.task_main(json.dumps(ct.replay()[1]["completed_jobs"]))["runs"] == ["9103", "9203"]
    result = json.loads((FEATURE / "result.json").read_text())
    assert {k: v for k, v in result.items() if k != "engine"} == json.loads(json.dumps(ct.summarise()))
    assert (result["cases_received"], result["cases_to_shelf"]) == (120, 88)


def test_job_settings():
    s = ct.job_settings("42", ["7", "8"], {"task_key": "main"}, {"environment_key": "e"})
    jc = s["trigger"]["job_completion"]
    assert jc == {"jobs": [{"workspace_id": "42", "job_id": "7"}, {"workspace_id": "42", "job_id": "8"}],
                  "if_status": "SUCCESS"}
    assert s["trigger"]["pause_status"] == "UNPAUSED" and "triggers" not in s and "schedule" not in s
    assert s["parameters"] == [{"name": "upstream_completions",
                                "default": "{{job.trigger.job_completion.completed_jobs}}"}]


def test_cloud_check():
    exp = [("7", "70"), ("8", "80")]
    ok = [{"job_id": 7, "run_id": 70, "termination_code": "SUCCESS"},
          {"job_id": 8, "run_id": 80, "termination_code": "SUCCESS_WITH_FAILURES"}]
    assert rc.check(ok, exp) == []
    assert "other order" in rc.check(ok[::-1], exp)[0]
    assert rc.check([ok[0], {**ok[1], "termination_code": "FAILED"}], exp)
    assert rc.from_logs("x\nDAY37_COMPLETIONS [{\"job_id\": \"7\"}]\n") == [{"job_id": "7"}]
    assert rc.redact("run 1234567 by me@x.com in /Users/me@x.com/f") == "run <id> by <user> in /Users/<me>/f"
