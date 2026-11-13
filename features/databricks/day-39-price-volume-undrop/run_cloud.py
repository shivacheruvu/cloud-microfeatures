"""Day 39 (Databricks) cloud run: put three store price files in a fresh managed volume (with a comment and a READ
VOLUME grant to account users), then submit undrop_volume.py as a one-off serverless job (environment version 6) that
snapshots the volume, DROPs it, finds it with SHOW VOLUMES DROPPED and brings it back with UNDROP VOLUME WITH ID
(Unity Catalog, 2026-11-07). The snapshots before the drop and after the undrop must both equal the local reference
(result.json), and the undropped volume must keep its ID and comment (checked over REST).

  python run_cloud.py     # needs DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions)

REST + one serverless job, no SQL warehouse. Folder, volumes and schema are deleted on exit (a dropped volume stays
recoverable for 7 days and is then purged). The workspace hostname and user name are never printed.
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
import undrop_volume as uv  # noqa: E402

HERE = Path(__file__).resolve().parent
SCHEMA, VOLUME, RUN_VOLUME, FOLDER = "cmf_day39", "price_files", "run", "cmf-day39"
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


def submit_payload(script: str, catalog: str, volume_id: str, workdir: str) -> dict:
    return {
        "run_name": "cloud-microfeatures day 39 drop and UNDROP a price-file volume",
        "timeout_seconds": TIMEOUT_SECONDS,
        "tasks": [{
            "task_key": "undrop_volume",
            "spark_python_task": {"python_file": script, "source": "WORKSPACE",
                                  "parameters": [catalog, SCHEMA, VOLUME, volume_id, workdir]},
            "environment_key": "serverless_v6",
        }],
        "environments": [{"environment_key": "serverless_v6", "spec": {"environment_version": ENV_VERSION}}],
    }


def from_logs(logs: str | None) -> dict | None:
    for line in reversed((logs or "").splitlines()):
        if line.startswith("DAY39_RESULT "):
            try:
                return json.loads(line[len("DAY39_RESULT "):])
            except ValueError:
                return None
    return None


def compare(cloud: dict, local: dict, rest_after: dict, volume_id: str) -> list[str]:
    """Before the drop and after the undrop, the volume must hold the same bytes Spark reads the same way, with its
    grant; after the undrop it must have the same ID and comment."""
    errors = []
    want = {f["file"]: f for f in local["files"]}
    if not cloud.get("undropped"):
        errors.append("UNDROP VOLUME did not run")
    for when in ("before", "after"):
        snap = cloud.get(when) or {}
        got = {f.get("file"): f for f in snap.get("files") or []}
        if set(got) != set(want):
            errors.append(f"{when}: files {sorted(map(str, got))} vs local {sorted(want)}")
            continue
        for name, w in want.items():
            g = got[name]
            for k in ("sha256", "rows", "price_total"):
                if g.get(k) != w[k]:
                    errors.append(f"{when}: {name} {k} {g.get(k)} vs local {w[k]}")
            if g.get("spark_rows") != w["rows"] or g.get("spark_price_total") != w["price_total"]:
                errors.append(f"{when}: Spark read {name} as {g.get('spark_rows')} rows / {g.get('spark_price_total')}"
                              f" vs local {w['rows']} / {w['price_total']}")
        if [uv.GRANTEE, uv.PRIVILEGE] not in (snap.get("grants") or []):
            errors.append(f"{when}: grant {uv.PRIVILEGE} to {uv.GRANTEE} missing ({snap.get('grants')})")
    if rest_after.get("volume_id") != volume_id:
        errors.append("the undropped volume has a different ID")
    if (rest_after.get("comment") or (cloud.get("after") or {}).get("comment")) != local["comment"]:
        errors.append(f"comment after undrop {rest_after.get('comment')!r} vs {local['comment']!r}")
    return errors


def compute_refused(e: "ApiError") -> bool:
    """The refusals seen since 2026-10-24 on the Free Edition workspace: FEATURE_DISABLED on job runs and 403
    'Organization ... has been cancelled or is not active'."""
    m = e.code.lower()
    return e.code.startswith("FEATURE_DISABLED") or "not active" in m or "cancelled" in m


def run() -> tuple[dict, dict, str]:
    catalog = api("GET", "/api/2.1/unity-catalog/current-metastore-assignment").get("default_catalog_name") or "workspace"
    user = api("GET", "/api/2.0/preview/scim/v2/Me").get("userName")
    if not user:
        raise ApiError(0, "no user name for the token", "scim Me")
    folder = f"/Users/{user}/{FOLDER}"
    script = f"/Workspace{folder}/undrop_volume.py"
    full = f"{catalog}.{SCHEMA}.{VOLUME}"
    workdir = f"/Volumes/{catalog}/{SCHEMA}/{RUN_VOLUME}/run"
    api("POST", "/api/2.1/unity-catalog/schemas", {"name": SCHEMA, "catalog_name": catalog,
                                                   "comment": "cloud-microfeatures day 39 scratch"}, ok=(200, 409))
    try:
        api("DELETE", f"/api/2.1/unity-catalog/volumes/{full}", ok=(200, 204, 404))  # a leftover from a failed run
        volume_id = api("POST", "/api/2.1/unity-catalog/volumes", {
            "catalog_name": catalog, "schema_name": SCHEMA, "name": VOLUME, "volume_type": "MANAGED",
            "comment": uv.COMMENT})["volume_id"]
        api("POST", "/api/2.1/unity-catalog/volumes", {"catalog_name": catalog, "schema_name": SCHEMA,
                                                       "name": RUN_VOLUME, "volume_type": "MANAGED"}, ok=(200, 409))
        api("PATCH", f"/api/2.1/unity-catalog/permissions/volume/{full}",
            {"changes": [{"principal": uv.GRANTEE, "add": [uv.PRIVILEGE.replace(" ", "_")]}]})
        for p in uv.price_files():
            s, raw = _request("PUT", "/api/2.0/fs/files" + urllib.parse.quote(
                f"/Volumes/{catalog}/{SCHEMA}/{VOLUME}/{p.name}") + "?overwrite=true", p.read_bytes(),
                {"Content-Type": "application/octet-stream"})
            if s not in (200, 201, 204):
                raise ApiError(s, raw.decode(errors="replace")[:200], "PUT price file")
        api("POST", "/api/2.0/workspace/mkdirs", {"path": folder})
        api("POST", "/api/2.0/workspace/import", {
            "path": f"{folder}/undrop_volume.py", "format": "AUTO", "overwrite": True,
            "content": base64.b64encode((HERE / "undrop_volume.py").read_bytes()).decode()})
        run_id = api("POST", "/api/2.2/jobs/runs/submit", submit_payload(script, catalog, volume_id, workdir))["run_id"]
        print(f"::notice title=Databricks day 39::serverless job submitted (environment version {ENV_VERSION})")
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
        rest_after = api("GET", f"/api/2.1/unity-catalog/volumes/{full}", ok=(200, 404))
        return result, rest_after, volume_id
    finally:  # leave nothing behind
        for method, path, body in (
                ("POST", "/api/2.0/workspace/delete", {"path": folder, "recursive": True}),
                ("DELETE", f"/api/2.1/unity-catalog/volumes/{full}", None),
                ("DELETE", f"/api/2.1/unity-catalog/volumes/{catalog}.{SCHEMA}.{RUN_VOLUME}", None),
                ("DELETE", f"/api/2.1/unity-catalog/schemas/{catalog}.{SCHEMA}?force=true", None)):
            try:
                api(method, path, body, ok=(200, 204, 404))
            except ApiError as e:
                print(f"::warning title=Databricks day 39 cleanup::{method} {path.split('/')[4]} returned "
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
        cloud, rest_after, volume_id = run()
    except ApiError as e:
        if compute_refused(e):
            print(f"::warning title=Databricks day 39::the workspace refused the job ({redact(str(e))}); the account "
                  "looks inactive, so the drop / UNDROP VOLUME round trip was verified locally only (nothing was "
                  "left behind)")
            return 0
        print(f"::error title=Databricks day 39 failed::{redact(str(e))}")
        return 1
    errors = compare(cloud, local, rest_after, volume_id)
    for e in errors:
        print(f"::error title=Databricks day 39 differs from local::{redact(e)}")
    if errors:
        return 1
    print(f"::notice title=Databricks day 39::{cloud.get('engine', 'serverless')} dropped the price-file volume "
          f"(files readable after the drop: {cloud.get('readable_after_drop')}), found it in SHOW VOLUMES DROPPED "
          f"({cloud.get('same_name_dropped')} dropped volume(s) with that name) and UNDROP VOLUME WITH ID brought back "
          f"all {local['file_count']} files byte for byte ({local['rows']} rows, prices total {local['price_total']}), "
          f"the comment, the {uv.PRIVILEGE} grant to {uv.GRANTEE} and the same volume ID; job took "
          f"{cloud.get('_seconds')}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
