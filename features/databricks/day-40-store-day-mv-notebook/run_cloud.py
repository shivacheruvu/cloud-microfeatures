"""Day 40 (Databricks) cloud run: submit store_day_mv.py as a one-off serverless job (environment version 6) that
creates a till-receipts table, a standalone materialized view of units and revenue per store per day with
CREATE MATERIALIZED VIEW sent through spark.sql (standalone pipelines on serverless general compute, GA 2026-11-11),
reads it, adds late receipts, reads it again (it must still give the old answer), runs REFRESH MATERIALIZED VIEW and
reads it a third time. The three reads must equal the local reference (result.json).

  python run_cloud.py     # needs DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions)

REST + one serverless job, no SQL warehouse. The workspace folder and the schema (table, view, run volume) are deleted
on exit. The workspace hostname and user name are never printed.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store_day_mv as mv  # noqa: E402

HERE = Path(__file__).resolve().parent
SCHEMA, RUN_VOLUME, FOLDER = "cmf_day40", "run", "cmf-day40"
ENV_VERSION = "6"
POLL_SECONDS, TIMEOUT_SECONDS = 15, 1800
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


def submit_payload(script: str, catalog: str, workdir: str) -> dict:
    return {
        "run_name": "cloud-microfeatures day 40 store-day sales materialized view on serverless",
        "timeout_seconds": TIMEOUT_SECONDS,
        "tasks": [{
            "task_key": "store_day_mv",
            "spark_python_task": {"python_file": script, "source": "WORKSPACE",
                                  "parameters": [catalog, SCHEMA, workdir]},
            "environment_key": "serverless_v6",
        }],
        "environments": [{"environment_key": "serverless_v6", "spec": {"environment_version": ENV_VERSION}}],
    }


def from_logs(logs: str | None) -> dict | None:
    for line in reversed((logs or "").splitlines()):
        if line.startswith("DAY40_RESULT "):
            try:
                return json.loads(line[len("DAY40_RESULT "):])
            except ValueError:
                return None
    return None


def compare(cloud: dict, local: dict) -> list[str]:
    """Before the refresh the view must hold the first rollup, also after the late receipts land; after REFRESH it
    must hold the new rollup."""
    errors = []
    for got_key, want_key in (("before_refresh", "before_refresh"), ("stale_before_refresh", "before_refresh"),
                              ("after_refresh", "after_refresh")):
        got, want = cloud.get(got_key), local[want_key]
        if got != want:
            g = {(r.get("store"), r.get("sale_date")): r for r in got or []}
            w = {(r["store"], r["sale_date"]): r for r in want}
            diff = [f"{k[0]} {k[1]}: {g.get(k)} vs local {w.get(k)}" for k in sorted(set(g) | set(w), key=str)
                    if g.get(k) != w.get(k)]
            errors.append(f"{got_key}: " + ("; ".join(diff)[:500] or "row order differs"))
    return errors


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
    script = f"/Workspace{folder}/store_day_mv.py"
    workdir = f"/Volumes/{catalog}/{SCHEMA}/{RUN_VOLUME}/run"
    api("POST", "/api/2.1/unity-catalog/schemas", {"name": SCHEMA, "catalog_name": catalog,
                                                   "comment": "cloud-microfeatures day 40 scratch"}, ok=(200, 409))
    try:
        api("POST", "/api/2.1/unity-catalog/volumes", {"catalog_name": catalog, "schema_name": SCHEMA,
                                                       "name": RUN_VOLUME, "volume_type": "MANAGED"}, ok=(200, 409))
        api("POST", "/api/2.0/workspace/mkdirs", {"path": folder})
        api("POST", "/api/2.0/workspace/import", {
            "path": f"{folder}/store_day_mv.py", "format": "AUTO", "overwrite": True,
            "content": base64.b64encode((HERE / "store_day_mv.py").read_bytes()).decode()})
        run_id = api("POST", "/api/2.2/jobs/runs/submit", submit_payload(script, catalog, workdir))["run_id"]
        print(f"::notice title=Databricks day 40::serverless job submitted (environment version {ENV_VERSION})")
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
        s, raw = _request("GET", "/api/2.0/fs/files" + urllib.parse.quote(f"{workdir}/result.json"))
        result = json.loads(raw) if s == 200 else from_logs(output.get("logs"))
        if result is None:
            raise ApiError(s, "job succeeded but its result file and logs couldn't be read", "result")
        result["_seconds"] = took
        return result
    finally:  # leave nothing behind
        for method, path, body in (
                ("POST", "/api/2.0/workspace/delete", {"path": folder, "recursive": True}),
                ("DELETE", f"/api/2.1/unity-catalog/tables/{catalog}.{SCHEMA}.{mv.VIEW}", None),
                ("DELETE", f"/api/2.1/unity-catalog/tables/{catalog}.{SCHEMA}.{mv.TABLE}", None),
                ("DELETE", f"/api/2.1/unity-catalog/volumes/{catalog}.{SCHEMA}.{RUN_VOLUME}", None),
                ("DELETE", f"/api/2.1/unity-catalog/schemas/{catalog}.{SCHEMA}?force=true", None)):
            try:
                api(method, path, body, ok=(200, 204, 404))
            except ApiError as e:
                print(f"::warning title=Databricks day 40 cleanup::{method} {path.split('/')[4]} returned "
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
            print(f"::warning title=Databricks day 40::the workspace refused the job ({redact(str(e))}); the account "
                  "looks inactive, so the materialized view create / stale read / REFRESH round trip was verified "
                  "locally only (nothing was left behind)")
            return 0
        print(f"::error title=Databricks day 40 failed::{redact(str(e))}")
        return 1
    errors = compare(cloud, local)
    for e in errors:
        print(f"::error title=Databricks day 40 differs from local::{redact(e)}")
    if errors:
        return 1
    tb, ta = local["totals_before"], local["totals_after"]
    print(f"::notice title=Databricks day 40::{cloud.get('engine', 'serverless')} created a standalone materialized "
          f"view with spark.sql (table type {cloud.get('table_type')}, {cloud.get('create_seconds')}s): "
          f"{tb['store_days']} store-days, {tb['units']} units, revenue {tb['revenue_cents'] / 100:.2f}, equal to "
          f"local; after {local['rows_batch_2']} late receipts it still gave that answer until REFRESH MATERIALIZED "
          f"VIEW ({cloud.get('refresh_seconds')}s) moved it to {ta['store_days']} store-days, {ta['units']} units, "
          f"revenue {ta['revenue_cents'] / 100:.2f}, equal to local; job took {cloud.get('_seconds')}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
