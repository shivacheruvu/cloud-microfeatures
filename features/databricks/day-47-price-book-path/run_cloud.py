"""Day 47 (Databricks) cloud run: submit path_prices.py as a one-off serverless job (environment version 6, Databricks
Runtime 19) that builds a live and a promo price book in two scratch schemas and prices one basket with an unqualified
query under four SET PATH settings, plus two refused SET PATH forms. Checks: every total, every current_path() string,
both error classes and the unchanged path equal the local Python model."""
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
VOLUME, FOLDER = "scratch", "cmf-day47"
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


def submit_payload(script: str, workdir: str, catalog: str) -> dict:
    return {
        "run_name": "cloud-microfeatures day 47 price book path",
        "timeout_seconds": TIMEOUT_SECONDS,
        "tasks": [{
            "task_key": "price_book_path",
            "spark_python_task": {"python_file": script, "source": "WORKSPACE", "parameters": [workdir, catalog]},
            "environment_key": "serverless_v6",
        }],
        "environments": [{"environment_key": "serverless_v6", "spec": {"environment_version": ENV_VERSION}}],
    }


def from_logs(logs: str | None) -> dict | None:
    for line in reversed((logs or "").splitlines()):
        if line.startswith("DAY47_RESULT "):
            try:
                return json.loads(line[len("DAY47_RESULT "):])
            except ValueError:
                return None
    return None


def _feature():
    import importlib.util
    spec = importlib.util.spec_from_file_location("day47_path_prices", HERE / "path_prices.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def compare(cloud: dict, catalog: str) -> list[str]:
    ref = _feature().reference(catalog)
    errors = []
    for part in ("totals", "current_path", "errors", "path_unchanged_after_errors"):
        if cloud.get(part) != ref[part]:
            errors.append(f"{part}: cloud {json.dumps(cloud.get(part))[:350]} vs local {json.dumps(ref[part])[:350]}")
    return errors


def compute_refused(e: "ApiError") -> bool:
    """The refusals seen since 2026-10-24 on the Free Edition workspace: FEATURE_DISABLED on job runs and 403
    'Organization ... has been cancelled or is not active'."""
    m = e.code.lower()
    return e.code.startswith("FEATURE_DISABLED") or "not active" in m or "cancelled" in m


def run() -> dict:
    feat = _feature()
    SCHEMA = feat.LIVE
    catalog = api("GET", "/api/2.1/unity-catalog/current-metastore-assignment").get("default_catalog_name") or "workspace"
    user = api("GET", "/api/2.0/preview/scim/v2/Me").get("userName")
    if not user:
        raise ApiError(0, "no user name for the token", "scim Me")
    folder = f"/Users/{user}/{FOLDER}"
    script = f"/Workspace{folder}/path_prices.py"
    workdir = f"/Volumes/{catalog}/{SCHEMA}/{VOLUME}/run"
    out_path = f"{workdir}/result.json"
    for name in (feat.LIVE, feat.PROMO):
        api("POST", "/api/2.1/unity-catalog/schemas", {"name": name, "catalog_name": catalog,
                                                       "comment": "cloud-microfeatures day 47 scratch"}, ok=(200, 409))
    try:
        api("POST", "/api/2.1/unity-catalog/volumes", {"catalog_name": catalog, "schema_name": SCHEMA,
                                                       "name": VOLUME, "volume_type": "MANAGED"}, ok=(200, 409))
        api("POST", "/api/2.0/workspace/mkdirs", {"path": folder})
        api("POST", "/api/2.0/workspace/import", {
            "path": f"{folder}/path_prices.py", "format": "AUTO", "overwrite": True,
            "content": base64.b64encode((HERE / "path_prices.py").read_bytes()).decode()})
        run_id = api("POST", "/api/2.2/jobs/runs/submit", submit_payload(script, workdir, catalog))["run_id"]
        print(f"::notice title=Databricks day 47::serverless job submitted (environment version {ENV_VERSION})")
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
        result["_catalog"] = catalog
        return result
    finally:  # leave nothing behind
        for method, path, body in (
                ("POST", "/api/2.0/workspace/delete", {"path": folder, "recursive": True}),
                ("DELETE", f"/api/2.1/unity-catalog/volumes/{catalog}.{SCHEMA}.{VOLUME}", None),
                ("DELETE", f"/api/2.1/unity-catalog/schemas/{catalog}.{feat.PROMO}?force=true", None),
                ("DELETE", f"/api/2.1/unity-catalog/schemas/{catalog}.{SCHEMA}?force=true", None)):
            try:
                api(method, path, body, ok=(200, 204, 404))
            except ApiError as e:
                print(f"::warning title=Databricks day 47 cleanup::{method} {path.split('/')[4]} returned "
                      f"{e.status}")


def redact(text: str) -> str:
    for secret in (os.environ.get("DATABRICKS_HOST", "").removeprefix("https://").rstrip("/"),):
        if secret:
            text = text.replace(secret, "<host>")
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<user>", text)
    text = re.sub(r"/Users/[^/\s\"']+", "/Users/<me>", text)
    return re.sub(r"\b\d{7,}\b", "<id>", text)  # workspace / organization IDs


def main() -> int:
    try:
        cloud = run()
    except ApiError as e:
        if compute_refused(e):
            print(f"::warning title=Databricks day 47::the workspace refused the job ({redact(str(e))}); the account "
                  "looks inactive, so SET PATH resolution was verified locally only (nothing was left behind)")
            return 0
        print(f"::error title=Databricks day 47 failed::{redact(str(e))}")
        return 1
    errors = compare(cloud, cloud["_catalog"])
    for e in errors:
        print(f"::error title=Databricks day 47 differs from local::{redact(e)}")
    if errors:
        return 1
    t = cloud["totals"]
    print(f"::notice title=Databricks day 47::{cloud.get('engine', 'SET PATH')}: one unqualified basket query priced "
          f"at {t['live']} cents on the live path, {t['promo_first']} with the promo schema first (basket still found "
          f"in live), {t['missing_skipped']} with a missing schema in the path (skipped); a view created under the "
          f"promo path kept {t['view_captured']} after SET PATH back to live (direct query {t['direct_after_view']}); "
          f"one-part and duplicate entries refused ({cloud['errors']['one_part']}, {cloud['errors']['duplicate']}), "
          f"path unchanged. All current_path() strings matched. Job took {cloud.get('_seconds')}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
