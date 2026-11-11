"""Day 37 (Databricks) cloud run: two upstream "receiving" jobs and a downstream "restock plan" job with a job completion
trigger on both (Lakeflow Jobs, Beta, platform release notes 2026-11-03), all serverless.

  python run_cloud.py     # needs DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions)

Runs each upstream job once, waits for the trigger to start the downstream job, and checks the run's
trigger_info.job_completion.completed_jobs (and the task's upstream_completions parameter, when its logs are readable)
lists exactly those two runs, in configuration order, with SUCCESS: what the local model (completion_trigger.py) says.
REST only. All three jobs and the workspace folder are deleted on exit. If the workspace refuses the trigger (the Beta
is switched on per workspace on the Previews page) or refuses compute (the Free Edition account has been inactive since
2026-10-24), the run warns and leaves nothing behind. The hostname, user name and IDs are never printed.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import completion_trigger as ct  # noqa: E402

FOLDER = "cmf-day37"
ENV = {"environment_key": "serverless_v6", "spec": {"environment_version": "6"}}
POLL_SECONDS, RUN_TIMEOUT, TRIGGER_TIMEOUT = 15, 1500, 900
SCHEME = "https"


class ApiError(Exception):
    def __init__(self, status: int, code: str, where: str = ""):
        super().__init__(f"{where} {status} {code}".strip())
        self.status, self.code = status, code


class TriggerRefused(Exception):
    pass


def _host() -> str:
    return os.environ["DATABRICKS_HOST"].rstrip("/").removeprefix("https://")


def api(method: str, path: str, body: dict | None = None, ok=(200,)) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{SCHEME}://{_host()}{path}", method=method, data=data, headers={
        "Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}", "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        s, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        s, raw = e.code, e.read()
    if s not in ok:
        try:
            err = json.loads(raw)
            code = f"{err.get('error_code', '')}: {str(err.get('message', ''))[:200]}"
        except ValueError:
            code = ""
        raise ApiError(s, code, f"{method} {path.split('?')[0]}")
    try:
        return json.loads(raw) if raw and raw.strip() else {}
    except ValueError:
        return {}


def task(script: str, params: list[str]) -> dict:
    return {"task_key": "main", "environment_key": ENV["environment_key"],
            "spark_python_task": {"python_file": script, "source": "WORKSPACE", "parameters": params}}


def upstream_settings(name: str, script: str) -> dict:
    return {"name": f"cloud-microfeatures day 37 {name}", "max_concurrent_runs": 1,
            "tags": {"project": "cloud-microfeatures", "day": "37"},
            "tasks": [task(script, ["receive", name])], "environments": [ENV]}


def wait_run(run_id: int, timeout: int = RUN_TIMEOUT) -> dict:
    deadline, info = time.time() + timeout, {}
    while time.time() < deadline:
        info = api("GET", f"/api/2.2/jobs/runs/get?run_id={run_id}")
        if (info.get("state") or {}).get("life_cycle_state") in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            return info
        time.sleep(POLL_SECONDS)
    raise ApiError(0, "timed out waiting for the run", "runs/get")


def result_code(info: dict) -> str:
    st = info.get("status") or {}
    term = (st.get("termination_details") or {}).get("code")
    return term or (info.get("state") or {}).get("result_state") or "UNKNOWN"


def check(completed: list[dict], expected: list[tuple[str, str]]) -> list[str]:
    """completed_jobs from the platform vs expected [(job_id, run_id)] in configuration order."""
    got = [(str(c.get("job_id")), str(c.get("run_id"))) for c in completed]
    errors = [] if got == expected else [f"completed_jobs is {len(got)} entries, not the two upstream runs in order"
                                         f"{' (same runs, other order)' if sorted(got) == sorted(expected) else ''}"]
    bad = [c.get("termination_code") for c in completed if not ct.matches("SUCCESS", str(c.get("termination_code")))]
    if bad:
        errors.append(f"termination codes {bad} should not match if_status SUCCESS")
    return errors


def from_logs(logs: str | None) -> list | None:
    for line in reversed((logs or "").splitlines()):
        if line.startswith("DAY37_COMPLETIONS "):
            try:
                return json.loads(line[len("DAY37_COMPLETIONS "):])
            except ValueError:
                return None
    return None


def compute_refused(e: ApiError) -> bool:
    m = e.code.lower()
    return e.code.startswith("FEATURE_DISABLED") or "not active" in m or "cancelled" in m


def run() -> dict:
    ws = api("GET", "/api/2.1/unity-catalog/current-metastore-assignment").get("workspace_id")
    user = api("GET", "/api/2.0/preview/scim/v2/Me").get("userName")
    if not user or not ws:
        raise ApiError(0, "no user name or workspace id for the token", "setup")
    folder = f"/Users/{user}/{FOLDER}"
    script = f"/Workspace{folder}/task_restock.py"
    jobs: list[int] = []
    try:
        api("POST", "/api/2.0/workspace/mkdirs", {"path": folder})
        api("POST", "/api/2.0/workspace/import", {
            "path": f"{folder}/task_restock.py", "format": "AUTO", "overwrite": True,
            "content": base64.b64encode((HERE / "task_restock.py").read_bytes()).decode()})
        for name in ("receive-dc-truck", "receive-dsd-vendors"):
            jobs.append(api("POST", "/api/2.2/jobs/create", upstream_settings(name, script))["job_id"])
        settings = ct.job_settings(str(ws), [str(j) for j in jobs],
                                   task(script, ["plan", "{{job.parameters.upstream_completions}}"]), ENV)
        try:
            down = api("POST", "/api/2.2/jobs/create", settings)["job_id"]
        except ApiError as e:
            if compute_refused(e):
                raise
            raise TriggerRefused(str(e)) from e
        jobs.append(down)
        time.sleep(30)   # let the trigger initialise: completions from before it exists never start a run
        expected = []
        for j in jobs[:2]:
            run_id = api("POST", "/api/2.2/jobs/run-now", {"job_id": j})["run_id"]
            info = wait_run(run_id)
            if (info.get("state") or {}).get("result_state") != "SUCCESS":
                raise ApiError(0, f"upstream run ended {result_code(info)}: "
                                  f"{str((info.get('state') or {}).get('state_message'))[:300]}", "upstream job")
            expected.append((str(j), str(run_id)))
        print("::notice title=Databricks day 37::both upstream receiving jobs ran on serverless; waiting for the job "
              "completion trigger")
        deadline, runs = time.time() + TRIGGER_TIMEOUT, []
        while time.time() < deadline and not runs:
            runs = api("GET", f"/api/2.2/jobs/runs/list?job_id={down}&limit=5").get("runs") or []
            if not runs:
                time.sleep(POLL_SECONDS)
        if not runs:
            return {"fired": False, "expected": expected}
        info = wait_run(runs[-1]["run_id"])
        completed = ((info.get("trigger_info") or {}).get("job_completion") or {}).get("completed_jobs") or []
        task_run = next((t.get("run_id") for t in info.get("tasks") or []), info.get("run_id"))
        logs = api("GET", f"/api/2.2/jobs/runs/get-output?run_id={task_run}", ok=(200, 400, 404)).get("logs")
        return {"fired": True, "expected": expected, "completed": completed, "task_saw": from_logs(logs),
                "trigger": info.get("trigger"), "result": (info.get("state") or {}).get("result_state"),
                "count": len(runs)}
    finally:  # leave nothing behind (downstream first, so a deleted upstream can't confuse it)
        for j in reversed(jobs):
            try:
                api("POST", "/api/2.2/jobs/delete", {"job_id": j}, ok=(200, 404))
            except ApiError as e:
                print(f"::warning title=Databricks day 37 cleanup::job delete returned {e.status}")
        try:
            api("POST", "/api/2.0/workspace/delete", {"path": folder, "recursive": True}, ok=(200, 404))
        except ApiError as e:
            print(f"::warning title=Databricks day 37 cleanup::folder delete returned {e.status}")


def redact(text: str) -> str:
    host = os.environ.get("DATABRICKS_HOST", "").removeprefix("https://").rstrip("/")
    if host:
        text = text.replace(host, "<host>")
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<user>", text)
    text = re.sub(r"/Users/[^/\s\"']+", "/Users/<me>", text)
    return re.sub(r"\b\d{5,}\b", "<id>", text)


def main() -> int:
    try:
        r = run()
    except TriggerRefused as e:
        print(f"::warning title=Databricks day 37::the workspace refused the job completion trigger ({redact(str(e))}); "
              "turn on the Beta on the Previews page (Job completion triggers). Verified locally only; nothing left behind")
        return 0
    except ApiError as e:
        if compute_refused(e):
            print(f"::warning title=Databricks day 37::the workspace refused the jobs ({redact(str(e))}); the account "
                  "looks inactive, so the trigger rules were verified locally only (nothing was left behind)")
            return 0
        print(f"::error title=Databricks day 37 failed::{redact(str(e))}")
        return 1
    if not r["fired"]:
        print(f"::error title=Databricks day 37::both upstream jobs succeeded but the trigger started no run within "
              f"{TRIGGER_TIMEOUT // 60} minutes")
        return 1
    errors = check(r["completed"], r["expected"])
    if r["task_saw"] is not None and [(c["job_id"], c["run_id"]) for c in r["task_saw"]] != r["expected"]:
        errors.append("the task's upstream_completions parameter differs from trigger_info")
    if r["result"] != "SUCCESS":
        errors.append(f"downstream run ended {r['result']}")
    for e in errors:
        print(f"::error title=Databricks day 37 differs from local::{redact(e)}")
    if errors:
        return 1
    codes = ", ".join(str(c.get("termination_code")) for c in r["completed"])
    print(f"::notice title=Databricks day 37::the job completion trigger started the restock-plan job once both "
          f"receiving jobs finished (trigger {r['trigger']}, {r['count']} run): completed_jobs lists the 2 upstream runs "
          f"in configuration order ({codes}); task parameter "
          f"{'matched' if r['task_saw'] is not None else 'not readable from logs'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
