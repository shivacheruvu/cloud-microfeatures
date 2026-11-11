"""Day 37 (Databricks): start the restock plan only when BOTH receiving jobs (the DC truck and the direct-store-delivery
vendors) have finished, with a Lakeflow Jobs **job completion trigger** (Beta, platform release notes 2026-11-03).

  python completion_trigger.py      # replay a receiving day through the documented trigger rules, writes result.json

The downstream job carries
    "trigger": {"pause_status": "UNPAUSED",
                "job_completion": {"jobs": [{"workspace_id": W, "job_id": DC}, {"workspace_id": W, "job_id": DSD}],
                                   "if_status": "SUCCESS"}}
and the job parameter  upstream_completions = {{job.trigger.job_completion.completed_jobs}}.

`Trigger` is a stdlib model of the rules on the docs page (jobs/job-completion-triggers):
- if_status SUCCESS (default) matches SUCCESS and SUCCESS_WITH_FAILURES; FAILED matches anything else; DONE matches all;
- AND across monitored jobs: fire once every job has a new matching completion; if a job completes several times
  first, its latest matching completion is used; after firing, every job must complete again;
- completions from before the trigger was created never start a run;
- completed_jobs is a JSON array in configuration order with workspace_id, job_id, run_id, termination_code and
  completion_time.
The restock task then turns the cases received in the contributing runs into a shelf plan.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUCCESS_CODES = {"SUCCESS", "SUCCESS_WITH_FAILURES"}

WORKSPACE = "1001"                      # placeholder IDs for the local replay (the cloud run uses real ones)
DC_JOB, DSD_JOB = "501", "502"
JOB_NAMES = {DC_JOB: "receive-dc-truck", DSD_JOB: "receive-dsd-vendors"}
TRIGGER_CREATED = "2026-11-10T05:30"

# One receiving day: (time, job, run_id, termination_code, cases received by item)
EVENTS = [
    ("2026-11-10T05:05", DC_JOB, "9100", "SUCCESS", {"oat milk 64 oz": 30}),                       # before the trigger
    ("2026-11-10T06:00", DC_JOB, "9101", "SUCCESS", {"oat milk 64 oz": 36, "bananas 40 lb": 12}),
    ("2026-11-10T06:40", DC_JOB, "9102", "SUCCESS", {"oat milk 64 oz": 40, "bananas 40 lb": 12}),  # ASN corrected, re-run
    ("2026-11-10T07:10", DSD_JOB, "9201", "FAILED", {}),                                            # vendor file late
    ("2026-11-10T07:30", DSD_JOB, "9202", "SUCCESS", {"sourdough loaf": 24, "eggs 12 ct": 18}),
    ("2026-11-10T12:00", DSD_JOB, "9203", "SUCCESS", {"eggs 12 ct": 12}),                           # afternoon vendor drop
    ("2026-11-10T13:00", DC_JOB, "9103", "SUCCESS_WITH_FAILURES", {"bananas 40 lb": 8, "oat milk 64 oz": 6}),
]
SHELF_CAP = {"oat milk 64 oz": 24, "bananas 40 lb": 10, "sourdough loaf": 20, "eggs 12 ct": 30}   # cases per shelf
ON_SHELF = {"oat milk 64 oz": 4, "bananas 40 lb": 3, "sourdough loaf": 2, "eggs 12 ct": 9}


def matches(if_status: str, code: str) -> bool:
    if if_status == "SUCCESS":
        return code in SUCCESS_CODES
    if if_status == "FAILED":
        return code not in SUCCESS_CODES
    if if_status == "DONE":
        return True
    raise ValueError(f"unknown if_status {if_status!r}")


@dataclass
class Trigger:
    jobs: list[str]                       # monitored job IDs, in configuration order
    if_status: str = "SUCCESS"
    created: str = TRIGGER_CREATED
    workspace_id: str = WORKSPACE
    pending: dict = field(default_factory=dict)   # job -> latest matching completion since the last fire

    def __post_init__(self):
        if not 1 <= len(self.jobs) <= 10 or len(set(self.jobs)) != len(self.jobs):
            raise ValueError("a job completion trigger monitors 1 to 10 distinct jobs")
        matches(self.if_status, "SUCCESS")

    def completion(self, time: str, job: str, run_id: str, code: str) -> list[dict] | None:
        """Feed one upstream completion; returns completed_jobs when the downstream run starts."""
        if job not in self.jobs or time < self.created or not matches(self.if_status, code):
            return None
        self.pending[job] = {"workspace_id": self.workspace_id, "job_id": job, "run_id": run_id,
                             "termination_code": code, "completion_time": time}
        if all(j in self.pending for j in self.jobs):
            fired = [self.pending[j] for j in self.jobs]
            self.pending = {}
            return fired
        return None


def replay(if_status: str = "SUCCESS", events=EVENTS) -> list[dict]:
    t = Trigger([DC_JOB, DSD_JOB], if_status)
    runs = []
    for time, job, run_id, code, _ in events:
        fired = t.completion(time, job, run_id, code)
        if fired:
            runs.append({"started_after": time, "completed_jobs": fired})
    return runs


# ---------- the downstream task: completed_jobs -> restock plan
def restock_plan(completed_jobs: list[dict], events=EVENTS) -> dict:
    """Cases from the contributing upstream runs -> cases to the shelf now (up to capacity) and to the backroom."""
    cases = {run_id: c for _, _, run_id, _, c in events}
    received: dict[str, int] = {}
    for c in completed_jobs:
        for item, n in cases.get(c["run_id"], {}).items():
            received[item] = received.get(item, 0) + n
    plan = {}
    for item in sorted(received):
        room = max(SHELF_CAP[item] - ON_SHELF[item], 0)
        to_shelf = min(room, received[item])
        plan[item] = {"received": received[item], "to_shelf": to_shelf, "to_backroom": received[item] - to_shelf}
    return plan


def task_main(upstream_completions: str) -> dict:
    """What the downstream task does with the job parameter (a JSON string; [] for a manual run)."""
    jobs = json.loads(upstream_completions or "[]")
    if not jobs:
        return {"manual_run": True, "plan": {}}
    return {"manual_run": False, "runs": [c["run_id"] for c in jobs], "plan": restock_plan(jobs)}


def job_settings(workspace_id: str, upstream_ids: list[str], task: dict, env: dict) -> dict:
    """Downstream job settings for POST /api/2.2/jobs/create."""
    return {
        "name": "cloud-microfeatures day 37 restock plan",
        "max_concurrent_runs": 1,
        "tags": {"project": "cloud-microfeatures", "day": "37"},
        "trigger": {"pause_status": "UNPAUSED",
                    "job_completion": {"jobs": [{"workspace_id": str(workspace_id), "job_id": str(j)}
                                                for j in upstream_ids],
                                       "if_status": "SUCCESS"}},
        "parameters": [{"name": "upstream_completions", "default": "{{job.trigger.job_completion.completed_jobs}}"}],
        "tasks": [task],
        "environments": [env],
    }


def summarise() -> dict:
    by_filter = {f: replay(f) for f in ("SUCCESS", "DONE", "FAILED")}
    runs = by_filter["SUCCESS"]
    plans = [restock_plan(r["completed_jobs"]) for r in runs]
    return {
        "upstream_completions": len(EVENTS),
        "downstream_runs": {f: len(r) for f, r in by_filter.items()},
        "runs": [{"started_after": r["started_after"],
                  "uses": {JOB_NAMES[c["job_id"]]: f"{c['run_id']} {c['termination_code']}" for c in r["completed_jobs"]},
                  "plan": p} for r, p in zip(runs, plans)],
        "done_filter_first_run_uses": [c["run_id"] for c in by_filter["DONE"][0]["completed_jobs"]],
        "cases_received": sum(v["received"] for p in plans for v in p.values()),
        "cases_to_shelf": sum(v["to_shelf"] for p in plans for v in p.values()),
        "headline": f"{len(EVENTS)} receiving runs -> {len(runs)} restock-plan runs with if_status SUCCESS "
                    f"(DONE: {len(by_filter['DONE'])}, FAILED: {len(by_filter['FAILED'])}); the 06:00 DC run is "
                    f"superseded by its 06:40 re-run and the failed vendor run never counts",
    }


def main(argv: list[str]) -> int:
    s = summarise()
    (HERE / "result.json").write_text(json.dumps(
        {"engine": "stdlib model of the documented job completion trigger rules (local) / Lakeflow Jobs job completion "
                   "trigger on serverless (cloud)", **s}, indent=2) + "\n")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
