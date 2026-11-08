"""Day 34 (Databricks) cloud run: submit coupon_reach.py as a one-off serverless job (environment version 6) that
builds per-store tuple sketches of loyalty spend and a theta sketch of e-mailed customers, combines them with
tuple_intersection_theta_double / tuple_difference_theta_double / tuple_union_theta_double (Databricks SQL 2026.15,
2026-05-26), and check everything equals the local reference in result.json.

  python run_cloud.py     # needs DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions)

REST only, no SQL warehouse: the script goes into the caller's workspace folder, the result file into a scratch
volume, and the job is a runs/submit one-off (nothing left scheduled). Folder, volume and schema are deleted on exit.
The workspace hostname and user name are never printed.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA, VOLUME, FOLDER = "cmf_day34", "scratch", "cmf-day34"
ENV_VERSION = "6"
POLL_SECONDS, TIMEOUT_SECONDS = 15, 1500
SCHEME = "https"


class ApiError(Exception):
    def __init__(self, status: int, code: str, where: str = ""):
        super().__init__(f"{where} {status} {code}".strip())
        self.status, self.code = status, code


def _host() -> str:
    return os.environ["DATABRICKS_HOST"].rstrip("/").removeprefix("https://")


def _request(method: str, path: str, data: bytes | None = None, headers: dict | None = None):
    h = {"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}", **(headers or {})}
    req = urllib.request.Request(f"{SCHEME}://{_host()}{path}", method=method, data=data, headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def api(method: str, path: str, body: dict | None = None, ok=(200,)) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    s, raw = _request(method, path, data, {"Content-Type": "application/json"})
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


def submit_payload(script: str, workdir: str) -> dict:
    return {
        "run_name": "cloud-microfeatures day 34 coupon reach tuple x theta sketches",
        "timeout_seconds": TIMEOUT_SECONDS,
        "tasks": [{
            "task_key": "coupon_reach",
            "spark_python_task": {"python_file": script, "source": "WORKSPACE", "parameters": [workdir]},
            "environment_key": "serverless_v6",
        }],
        "environments": [{"environment_key": "serverless_v6", "spec": {"environment_version": ENV_VERSION}}],
    }


def from_logs(logs: str | None) -> dict | None:
    for line in reversed((logs or "").splitlines()):
        if line.startswith("DAY34_RESULT "):
            try:
                return json.loads(line[len("DAY34_RESULT "):])
            except ValueError:
                return None
    return None


def compare(cloud: dict, local: dict) -> list[str]:
    return [f"{k} cloud {json.dumps(cloud.get(k))[:300]} vs local {json.dumps(local[k])[:300]}"
            for k in ("purchases", "emailed", "stores", "chain") if cloud.get(k) != local[k]]


def compute_refused(e: "ApiError") -> bool:
    """The refusals seen since 2026-10-24 on the Free Edition workspace: FEATURE_DISABLED on job runs and 403
    'Organization ... has been cancelled or is not active'."""
    m = e.code.lower()
    return e.code.startswith("FEATURE_DISABLED") or "not active" in m or "cancelled" in m


def run() -> dict:
    catalog = api("GET", "/api/2.1/unity-catalog/current-metastore-assignment").get("default_catalog_name") or "workspace"
    user = api("GET", "/api/2.0/preview/scim/v2/Me").get("userName")
    if not user:
        raise ApiError(0, "no user name for the token", "scim Me")
    folder = f"/Users/{user}/{FOLDER}"
    script = f"/Workspace{folder}/coupon_reach.py"
    workdir = f"/Volumes/{catalog}/{SCHEMA}/{VOLUME}/run"
    out_path = f"{workdir}/result.json"
    api("POST", "/api/2.1/unity-catalog/schemas", {"name": SCHEMA, "catalog_name": catalog,
                                                   "comment": "cloud-microfeatures day 34 scratch"}, ok=(200, 409))
    try:
        api("POST", "/api/2.1/unity-catalog/volumes", {"catalog_name": catalog, "schema_name": SCHEMA,
                                                       "name": VOLUME, "volume_type": "MANAGED"}, ok=(200, 409))
        api("POST", "/api/2.0/workspace/mkdirs", {"path": folder})
        api("POST", "/api/2.0/workspace/import", {
            "path": f"{folder}/coupon_reach.py", "format": "AUTO", "overwrite": True,
            "content": base64.b64encode((HERE / "coupon_reach.py").read_bytes()).decode()})
        run_id = api("POST", "/api/2.2/jobs/runs/submit", submit_payload(script, workdir))["run_id"]
        print(f"::notice title=Databricks day 34::serverless job submitted (environment version {ENV_VERSION})")
        deadline, state, info = time.time() + TIMEOUT_SECONDS + 120, {}, {}
        while time.time() < deadline:
            info = api("GET", f"/api/2.2/jobs/runs/get?run_id={run_id}")
            state = info.get("state") or {}
            if state.get("life_cycle_state") in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
                break
            time.sleep(POLL_SECONDS)
        task_run = next((t.get("run_id") for t in info.get("tasks") or []), run_id)
        output = api("GET", f"/api/2.2/jobs/runs/get-output?run_id={task_run}", ok=(200, 400, 404))
        if state.get("result_state") != "SUCCESS":
            detail = output.get("error") or state.get("state_message") or state.get("life_cycle_state")
            raise ApiError(0, f"job ended {state.get('result_state') or state.get('life_cycle_state')}: "
                              f"{str(detail)[:400]}", "serverless job")
        took = round((info.get("end_time", 0) - info.get("start_time", 0)) / 1000)
        s, raw = _request("GET", "/api/2.0/fs/files" + urllib.parse.quote(out_path))
        result = json.loads(raw) if s == 200 else from_logs(output.get("logs"))
        if result is None:
            raise ApiError(s, "job succeeded but its result file and logs couldn't be read", "result")
        result["_seconds"] = took
        return result
    finally:  # leave nothing behind
        for method, path, body in (
                ("POST", "/api/2.0/workspace/delete", {"path": folder, "recursive": True}),
                ("DELETE", f"/api/2.1/unity-catalog/volumes/{catalog}.{SCHEMA}.{VOLUME}", None),
                ("DELETE", f"/api/2.1/unity-catalog/schemas/{catalog}.{SCHEMA}?force=true", None)):
            try:
                api(method, path, body, ok=(200, 204, 404))
            except ApiError as e:
                print(f"::warning title=Databricks day 34 cleanup::{method} {path.split('/')[4]} returned "
                      f"{e.status}")


def redact(text: str) -> str:
    for secret in (os.environ.get("DATABRICKS_HOST", "").removeprefix("https://").rstrip("/"),):
        if secret:
            text = text.replace(secret, "<host>")
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<user>", text)
    text = re.sub(r"/Users/[^/\s\"']+", "/Users/<me>", text)
    return re.sub(r"\b\d{7,}\b", "<id>", text)  # workspace / organization IDs


def main() -> int:
    local = json.loads((HERE / "result.json").read_text())
    try:
        cloud = run()
    except ApiError as e:
        if compute_refused(e):
            print(f"::warning title=Databricks day 34::the workspace refused the job ({redact(str(e))}); the account "
                  "looks inactive, so the tuple x theta sketch functions were verified locally only (nothing was left behind)")
            return 0
        print(f"::error title=Databricks day 34 failed::{redact(str(e))}")
        return 1
    errors = compare(cloud, local)
    for e in errors:
        print(f"::error title=Databricks day 34 differs from local::{redact(e)}")
    if errors:
        return 1
    c = local["chain"]
    print(f"::notice title=Databricks day 34::{cloud.get('engine', 'tuple x theta sketches')} (environment version "
          f"{ENV_VERSION}) matched local: of {c['buyers']} loyalty buyers (${c['spend_cents'] / 100:,.2f}) across "
          f"{len(local['stores'])} stores, the coupon e-mail reached {c['reached_buyers']} "
          f"(${c['reached_spend_cents'] / 100:,.2f}) and missed {c['missed_buyers']} "
          f"(${c['missed_spend_cents'] / 100:,.2f}); {c['touched']} customers touched in all; job took "
          f"{cloud.get('_seconds')}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
