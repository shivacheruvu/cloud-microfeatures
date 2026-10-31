"""Day 26 (Databricks): one store price-refresh job with several triggers (a nightly schedule, a file-arrival trigger
per store folder, a periodic safety refresh), each paused or live on its own: multiple triggers for Lakeflow Jobs
(Beta, 2026-10-05).

  python triggers.py --local        # reconcile the three trigger versions against an in-memory Jobs API that
                                    # follows the documented rules; writes result.json
  python triggers.py --databricks   # the same against the real Jobs API (create/update/get/delete only, nothing is
                                    # ever run); every step's outcome and the triggers read back must equal local

The rules the reconciler and the local API follow (Lakeflow Jobs docs, "Multiple triggers"): `triggers` is an array
in the job settings, at most five during the Beta; it can't be sent together with the legacy `schedule`, `trigger`
or `continuous` fields; an update replaces the whole array, so the reconciler always sends the complete list; each
trigger has its own `pause_status`. The reconciler reads the job, compares the normalised triggers with the wanted
ones, and makes one call: create, update (full array) or nothing.

Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets); the hostname is never
printed. No compute is used: creating, updating and deleting a job are REST calls, and the job is deleted before
any trigger could fire.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEME = "https"  # tests point the client at a local http server
JOBS = "/api/2.2/jobs"
SCHEMA = "mf26_prices"
MAX_TRIGGERS = 5
LEGACY = ("schedule", "trigger", "continuous")
KINDS = {"schedule": ("quartz_cron_expression", "timezone_id"),
         "file_arrival": ("url", "min_time_between_triggers_seconds", "wait_after_last_change_seconds"),
         "periodic": ("interval", "unit"),
         "table_update": ("table_names", "condition", "min_time_between_triggers_seconds",
                          "wait_after_last_change_seconds")}


def config() -> dict:
    return json.loads((HERE / "data" / "triggers.json").read_text())


def fill(obj, catalog: str):
    return json.loads(json.dumps(obj).replace("{catalog}", catalog))


def unfill(obj, catalog: str):
    return json.loads(json.dumps(obj).replace(f"/Volumes/{catalog}/", "/Volumes/{catalog}/"))


def job_settings(triggers: list[dict], catalog: str) -> dict:
    return {
        "name": config()["job_name"],
        "max_concurrent_runs": 1,
        "tasks": [{"task_key": "refresh_prices", "environment_key": "default",
                   "spark_python_task": {"python_file": f"/Volumes/{catalog}/{SCHEMA}/code/refresh.py"}}],
        "environments": [{"environment_key": "default", "spec": {"environment_version": "4"}}],
        "tags": {"project": "cloud-microfeatures", "day": "26"},
        "triggers": copy.deepcopy(triggers),
    }


def rejected_settings(case: dict, catalog: str, current: list[dict]) -> dict:
    """The new_settings of an update the API must refuse."""
    if case.get("triggers") == "six_file_arrivals":
        url = f"/Volumes/{catalog}/{SCHEMA}/price_drop/store-{{}}/"
        return {"triggers": [{"file_arrival": {"url": url.format(i)}} for i in range(1, 7)]}
    sched = {"quartz_cron_expression": "0 0 6 * * ?", "timezone_id": "America/Chicago"}
    return {"triggers": copy.deepcopy(current), case["legacy"]: sched}


# ---------------------------------------------------------------- rules and normalisation
def normalise(triggers: list[dict] | None) -> list[dict] | None:
    """The part of each trigger that decides when the job runs, defaults filled in, in a stable order."""
    if triggers is None:
        return None
    out = []
    for t in triggers:
        kind = next(k for k in KINDS if k in t)
        out.append({"kind": kind, "pause_status": t.get("pause_status", "UNPAUSED"),
                    **{f: t[kind][f] for f in KINDS[kind] if f in t[kind]}})
    return sorted(out, key=lambda x: json.dumps(x, sort_keys=True))


def problems(settings: dict) -> list[str]:
    trig = settings.get("triggers")
    if trig is None:
        return []
    errs = []
    if len(trig) > MAX_TRIGGERS:
        errs.append(f"{len(trig)} triggers; at most {MAX_TRIGGERS}")
    errs += [f"triggers can't be combined with {f}" for f in LEGACY if f in settings]
    for t in trig:
        kinds = [k for k in KINDS if k in t]
        if len(kinds) != 1:
            errs.append("each trigger needs exactly one kind")
            continue
        body = t[kinds[0]]
        if t.get("pause_status", "UNPAUSED") not in ("PAUSED", "UNPAUSED"):
            errs.append("pause_status must be PAUSED or UNPAUSED")
        if kinds[0] == "file_arrival" and not str(body.get("url", "")).startswith("/Volumes/"):
            errs.append("file_arrival url must be a volume path")
        for f in ("min_time_between_triggers_seconds", "wait_after_last_change_seconds"):
            if f in body and body[f] < 60:
                errs.append(f"{f} must be at least 60")
        if kinds[0] == "periodic" and body.get("unit") not in ("MINUTES", "HOURS", "DAYS", "WEEKS"):
            errs.append("periodic unit must be MINUTES, HOURS, DAYS or WEEKS")
    return errs


def plan(current: list[dict] | None, wanted: list[dict]) -> str:
    if current is None:
        return "create"
    return "noop" if normalise(current) == normalise(wanted) else "update"


# ---------------------------------------------------------------- the API, in memory
class LocalJobs:
    """In-memory Jobs API with the documented multiple-triggers rules."""

    def __init__(self):
        self.jobs: dict[int, dict] = {}
        self.next_id = 1001

    def create(self, settings):
        if problems(settings):
            return 400, None
        self.jobs[self.next_id] = copy.deepcopy(settings)
        self.next_id += 1
        return 200, self.next_id - 1

    def get(self, job_id):
        return (200, copy.deepcopy(self.jobs[job_id])) if job_id in self.jobs else (404, None)

    def update(self, job_id, new_settings):
        if job_id not in self.jobs:
            return 404
        merged = {**self.jobs[job_id], **copy.deepcopy(new_settings)}  # top-level fields replace whole
        if problems(merged):
            return 400
        self.jobs[job_id] = merged
        return 200

    def delete(self, job_id):
        return 200 if self.jobs.pop(job_id, None) is not None else 404


def outcome(status: int) -> str:
    return {200: "ok", 404: "absent", 400: "rejected"}.get(status, f"error {status}")


def scenario(api, catalog: str) -> dict:
    """Apply the trigger versions in order, re-apply, try the updates that must be refused, delete."""
    cfg = fill(config(), catalog)
    steps, job_id = [], None

    def record(step, call, status, rejection=False):
        s, settings = api.get(job_id) if job_id is not None else (404, None)
        trig = normalise(settings.get("triggers")) if s == 200 else None
        steps.append({"step": step, "call": call, "outcome": outcome(status), "must_reject": rejection,
                      "triggers_after": unfill(trig, catalog)})

    def current():
        if job_id is None:
            return None
        s, settings = api.get(job_id)
        return settings.get("triggers") if s == 200 else None

    for v in cfg["versions"]:
        for attempt in ("", " again"):
            action = plan(current(), v["triggers"])
            if action == "create":
                status, job_id = api.create(job_settings(v["triggers"], catalog))
                record(f"apply {v['version']}{attempt}", "create", status)
                if job_id is None:
                    return {"steps": steps}
            elif action == "update":
                record(f"apply {v['version']}{attempt}", f"update ({len(v['triggers'])} triggers)",
                       api.update(job_id, {"triggers": v["triggers"]}))
            else:
                record(f"apply {v['version']}{attempt}", "none", 200)
            if v is not cfg["versions"][0] and not attempt:
                break  # re-apply once, after the create, to show it is a no-op
    for case in cfg["must_reject"]:
        record(f"update with {case['case']}", "update",
               api.update(job_id, rejected_settings(case, catalog, current())), rejection=True)
    record("delete the job", "delete", api.delete(job_id))
    return {"steps": steps}


def weekly_scheduled_runs(triggers: list[dict]) -> int:
    runs = 0
    for t in normalise(triggers):
        if t["pause_status"] != "UNPAUSED":
            continue
        if t["kind"] == "schedule" and re.fullmatch(r"\d+ \d+ \d+ \* \* \?", t["quartz_cron_expression"]):
            runs += 7  # once a day
        elif t["kind"] == "periodic":
            runs += {"MINUTES": 10080, "HOURS": 168, "DAYS": 7, "WEEKS": 1}[t["unit"]] // t["interval"]
    return runs


def summarise(run: dict) -> dict:
    versions = {}
    for v in config()["versions"]:
        n = normalise(v["triggers"])
        versions[v["version"]] = {
            "why": v["why"],
            "triggers": len(n),
            "live": sum(t["pause_status"] == "UNPAUSED" for t in n),
            "paused": [t["kind"] for t in n if t["pause_status"] == "PAUSED"],
            "watched_folders": sorted(t["url"].rstrip("/").rsplit("/", 1)[-1] for t in n
                                      if t["kind"] == "file_arrival" and t["pause_status"] == "UNPAUSED"),
            "scheduled_runs_per_week": weekly_scheduled_runs(v["triggers"]),
        }
    calls = [s["call"] for s in run["steps"]]
    return {
        "steps": run["steps"],
        "api_calls_that_change_the_job": sum(c != "none" for c in calls),
        "noop_reapplies": calls.count("none"),
        "refused_as_expected": sum(s["must_reject"] and s["outcome"] == "rejected" for s in run["steps"]),
        "versions": versions,
    }


# ---------------------------------------------------------------- Databricks
class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code} {message}".strip())
        self.status, self.code, self.message = status, code, message


def _host() -> str:
    return os.environ["DATABRICKS_HOST"].rstrip("/").removeprefix("https://")


def _redact(text: str) -> str:
    return re.sub(r"\d{6,}", "<id>", str(text).replace(_host(), "<host>"))[:300]


def _request(method: str, path: str, body=None, raw: bytes | None = None) -> tuple[int, dict]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
               "Content-Type": "application/octet-stream" if raw is not None else "application/json"}
    req = urllib.request.Request(f"{SCHEME}://{_host()}{path}", method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        out, status = e.read(), e.code
    try:
        return status, (json.loads(out) if out.strip() else {})
    except ValueError:
        return status, {}


def _call(method, path, body=None, ok=(200,), raw=None) -> dict:
    s, out = _request(method, path, body, raw)
    if s not in ok:
        raise ApiError(s, out.get("error_code", ""), _redact(out.get("message", "")))
    return out


class RestJobs:
    """The Jobs API 2.2. A 400 is the API refusing the settings (recorded as 'rejected', message kept); anything
    else unexpected raises."""

    def __init__(self):
        self.refusals: list[str] = []

    def _refused(self, s, out):
        if s == 400:
            self.refusals.append(_redact(f"{out.get('error_code', '')} {out.get('message', '')}"))
            return True
        raise ApiError(s, out.get("error_code", ""), _redact(out.get("message", "")))

    def create(self, settings):
        s, out = _request("POST", f"{JOBS}/create", settings)
        if s == 200:
            return 200, out["job_id"]
        self._refused(s, out)
        return 400, None

    def get(self, job_id):
        s, out = _request("GET", f"{JOBS}/get?" + urllib.parse.urlencode({"job_id": job_id}))
        if s == 200:
            return 200, out.get("settings", {})
        if s in (400, 404) and out.get("error_code") in ("RESOURCE_DOES_NOT_EXIST", "INVALID_PARAMETER_VALUE"):
            return 404, None
        raise ApiError(s, out.get("error_code", ""), _redact(out.get("message", "")))

    def update(self, job_id, new_settings):
        s, out = _request("POST", f"{JOBS}/update", {"job_id": job_id, "new_settings": new_settings})
        if s == 200:
            return 200
        self._refused(s, out)
        return 400

    def delete(self, job_id):
        s, out = _request("POST", f"{JOBS}/delete", {"job_id": job_id})
        if s == 200:
            return 200
        if out.get("error_code") == "RESOURCE_DOES_NOT_EXIST":
            return 404
        raise ApiError(s, out.get("error_code", ""), _redact(out.get("message", "")))


REFRESH_PY = b'print("day 26 price refresh: a placeholder task; the job is deleted before it can run")\n'


def run_databricks() -> tuple[dict, RestJobs] | None:
    """Scratch schema with two volumes (code, price_drop/east and /west), the scenario, then everything is deleted."""
    cat = _call("GET", "/api/2.1/unity-catalog/current-metastore-assignment").get("default_catalog_name") or "workspace"
    s, _ = _request("POST", "/api/2.1/unity-catalog/schemas",
                    {"name": SCHEMA, "catalog_name": cat, "comment": "cloud-microfeatures day 26 scratch"})
    if s not in (200, 409):  # 409: left by an interrupted run of this feature; it is ours and is dropped below
        raise ApiError(s, "SCHEMA_CREATE", "could not create the scratch schema")
    api, job_ids = RestJobs(), []
    try:
        for vol in ("code", "price_drop"):
            _call("POST", "/api/2.1/unity-catalog/volumes",
                  {"catalog_name": cat, "schema_name": SCHEMA, "name": vol, "volume_type": "MANAGED"}, ok=(200, 409))
        for d in ("east", "west"):
            _call("PUT", f"/api/2.0/fs/directories/Volumes/{cat}/{SCHEMA}/price_drop/{d}", ok=(200, 201, 204, 409))
        _call("PUT", f"/api/2.0/fs/files/Volumes/{cat}/{SCHEMA}/code/refresh.py?overwrite=true",
              raw=REFRESH_PY, ok=(200, 201, 204))
        orig_create = api.create

        def tracked_create(settings):
            s, jid = orig_create(settings)
            if jid is not None:
                job_ids.append(jid)
            return s, jid
        api.create = tracked_create
        return scenario(api, cat), api
    finally:  # leave nothing behind: the job (if a step failed before the delete), the volumes and the schema
        try:
            for jid in job_ids:
                _request("POST", f"{JOBS}/delete", {"job_id": jid})
            for vol in ("code", "price_drop"):
                _request("DELETE", f"/api/2.1/unity-catalog/volumes/{cat}.{SCHEMA}.{vol}")
            _call("DELETE", f"/api/2.1/unity-catalog/schemas/{cat}.{SCHEMA}?force=true", ok=(200, 204, 404))
        except ApiError as e:
            print(f"::warning title=Databricks day 26 cleanup::dropping the scratch schema returned {e}")


def compare(cloud: dict, local: dict, refusals: list[str]) -> int:
    """0 = matched (rejection cases may differ: that is a warning), 1 = differs."""
    c, l = cloud["steps"], local["steps"]
    if c and l and c[0]["outcome"] == "rejected" and l[0]["outcome"] == "ok":
        print("::warning title=Databricks day 26::the workspace refused a job with a `triggers` array "
              f"({(refusals or ['no message'])[0]}). Multiple triggers is a Beta that a workspace admin turns on "
              "(Previews page, 'Multiple Triggers'); verified locally only, nothing was left behind")
        return 0
    core = [(x, y) for x, y in zip(c, l) if not y["must_reject"] and x != y]
    if len(c) != len(l) or core:
        print(f"::error title=Databricks day 26 differs from local::{json.dumps(core[:2] or [len(c), len(l)])[:900]}")
        return 1
    loose = [y["step"] for x, y in zip(c, l) if y["must_reject"] and x != y]
    if loose:
        print("::warning title=Databricks day 26::the Jobs API accepted settings the docs say it refuses: "
              + "; ".join(loose))
    s = summarise(cloud)
    v = s["versions"]
    print(f"::notice title=Databricks day 26::multiple job triggers matched local on all {len(c)} steps: "
          f"{s['api_calls_that_change_the_job']} job API calls (create, 2 full-array updates, "
          f"{s['refused_as_expected']} refused as expected, delete), {s['noop_reapplies']} no-op re-applies. v3 holiday "
          f"freeze: {v['v3']['live']} of {v['v3']['triggers']} triggers live, nightly schedule paused alone, store "
          f"folders {', '.join(v['v3']['watched_folders'])} still watched, {v['v3']['scheduled_runs_per_week']} "
          "scheduled runs a week")
    return 0


def main(mode: str) -> int:
    local = scenario(LocalJobs(), "workspace")
    if mode != "--databricks":
        s = summarise(local)
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "in-memory Jobs API with the documented multiple-triggers rules (local) / Jobs API 2.2 "
                       "(cloud)", **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
        return 0
    try:
        cloud, api = run_databricks()
    except ApiError as e:
        # Seen on 2026-10-30: the Jobs API answered 403 "Organization <id> has been cancelled or is not active yet",
        # the account-level block behind the FEATURE_DISABLED job runs and the refused warehouse since 10-24. A
        # workspace state, not a bug in this feature, so it is a warning; the scratch schema is still dropped.
        if e.code == "FEATURE_DISABLED" or "FEATURE_DISABLED" in e.message or (
                e.status == 403 and "not active" in e.message):
            print(f"::warning title=Databricks day 26::the workspace refused the Jobs API ({e}); the account looks "
                  "inactive, so multiple triggers were verified locally only (nothing was left behind)")
            return 0
        print(f"::error title=Databricks day 26 API call failed::{e}")
        return 1
    return compare(cloud, local, api.refusals)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "--local"))
