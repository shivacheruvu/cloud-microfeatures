"""Day 34 (Google Cloud) cloud part: create a Cloud Run job on Google's python315 runtime base image, execute it
once, read its DAY34_RESULT log line, compare with the local reference (result.json); delete the job on exit.

gcloud only (keyless sign-in in GitHub Actions). cloud.sh redacts the project ID, accounts and long numbers.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time

import rollup as ru

LOG_WAIT_SECONDS, LOG_POLL_SECONDS = 240, 15
COMMAND_ARG = "exec(__import__('base64').b64decode(__import__('os').environ.get('DAY34_CODE')))"  # no commas
# The runtime image's interpreter is expected on PATH as python3; the others are fallbacks tried in order.
COMMANDS = ("python3", "python")


class GcloudError(Exception):
    pass


def _gcloud(args: list[str], timeout: int = 900) -> str:
    p = subprocess.run(["gcloud", *args, "--quiet"], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        err = " ".join((p.stderr or "").split())
        raise GcloudError(f"gcloud {' '.join(a for a in args[:3] if not a.startswith('-'))}: {err[-500:]}")
    return p.stdout


def create_args(region: str, env_file: str, command: str = "python3", service_account: str | None = None) -> list[str]:
    args = ["run", "jobs", "create", ru.JOB, f"--region={region}", f"--image={ru.IMAGE}",
            f"--command={command}", f"--args=-c,{COMMAND_ARG}", f"--env-vars-file={env_file}",
            "--cpu=1", "--memory=512Mi", "--tasks=1", "--max-retries=0", "--task-timeout=300s",
            "--labels=app=cloud-microfeatures,day=34"]
    if service_account:
        args.append(f"--service-account={service_account}")
    return args


def env_file_text(code: bytes, data: bytes) -> str:
    return (f"DAY34_MODE: job\nDAY34_CODE: \"{base64.b64encode(code).decode()}\"\n"
            f"DAY34_DATA: \"{base64.b64encode(data).decode()}\"\n")


def log_filter(execution: str) -> str:
    return (f'resource.type="cloud_run_job" AND resource.labels.job_name="{ru.JOB}" AND '
            f'labels."run.googleapis.com/execution_name"="{execution}"')


def read_result(execution: str, gcloud=_gcloud, sleep=time.sleep, wait: int = LOG_WAIT_SECONDS):
    """Wait for the task's DAY34_RESULT line in Cloud Logging. Returns (result, last log lines)."""
    deadline, tail = time.time() + wait, []
    while True:
        raw = gcloud(["logging", "read", log_filter(execution), "--freshness=2h", "--limit=50", "--format=json"])
        texts = [e.get("textPayload") or "" for e in json.loads(raw or "[]")]
        for t in texts:
            if t.startswith("DAY34_RESULT "):
                return json.loads(t[len("DAY34_RESULT "):]), texts
        tail = [t for t in texts if t][:5]
        if time.time() >= deadline:
            return None, tail
        sleep(LOG_POLL_SECONDS)


def compare(cloud: dict, local: dict) -> list[str]:
    errors = [f"{k}: cloud {json.dumps(cloud.get(k))[:300]} vs local {json.dumps(local[k])[:300]}"
              for k in ru.KEYS if cloud.get(k) != local[k]]
    rt = cloud.get("runtime") or {}
    if not str(rt.get("python", "")).startswith("3.15."):
        errors.append(f"job ran Python {rt.get('python')}, expected 3.15.x from the {ru.RUNTIME_ID} image")
    if rt.get("utf8_mode") != 1 or not (rt.get("receipt") or {}).get("utf8"):
        errors.append(f"UTF-8 default not seen: utf8_mode={rt.get('utf8_mode')} receipt={rt.get('receipt')}")
    if not rt.get("frozendict_builtin") or not rt.get("lazy_import_deferred"):
        errors.append(f"frozendict / lazy import not seen: {json.dumps(rt)}")
    return errors


def cleanup(region: str, gcloud=_gcloud) -> None:
    try:
        names = gcloud(["run", "jobs", "list", f"--region={region}", "--filter=metadata.name~^cmf-d34-",
                        "--format=value(metadata.name)"]).split()
    except GcloudError as e:
        print(f"::warning title=Google Cloud day 34 cleanup::couldn't list jobs: {e}")
        return
    for n in names:
        try:
            gcloud(["run", "jobs", "delete", n, f"--region={region}"])
        except GcloudError as e:
            print(f"::warning title=Google Cloud day 34 cleanup::couldn't delete job {n}: {e}")


def create_job(region: str, env_file: str, gcloud=_gcloud) -> str:
    """Create with the default identity; if the default compute service account can't be used, run the job as the
    signed-in service account. Returns how it was created."""
    try:
        gcloud(create_args(region, env_file))
        return "default job identity"
    except GcloudError as e:
        msg = str(e).lower()
        if "service account" not in msg and "iam.serviceaccounts.actas" not in msg:
            raise
        account = gcloud(["config", "get-value", "account"]).strip()
        gcloud(create_args(region, env_file, service_account=account))
        return "signed-in service account as job identity"


def execute(region: str, gcloud=_gcloud, sleep=time.sleep, wait: int = LOG_WAIT_SECONDS):
    """Execute the job; if the task fails (e.g. interpreter not found under that name), switch to the next command
    and try again. Returns (result, command, seconds, notes)."""
    notes = []
    for i, cmd in enumerate(COMMANDS):
        if i:
            gcloud(["run", "jobs", "update", ru.JOB, f"--region={region}", f"--command={cmd}"])
        t0 = time.time()
        try:
            execution = gcloud(["run", "jobs", "execute", ru.JOB, f"--region={region}", "--wait",
                                "--format=value(metadata.name)"], timeout=900).strip()
        except GcloudError as e:
            notes.append(f"--command={cmd}: {str(e)[-250:]}")
            continue
        cloud, tail = read_result(execution, gcloud, sleep, wait)
        if cloud is not None:
            return cloud, cmd, round(time.time() - t0), notes
        notes.append(f"--command={cmd}: no DAY34_RESULT line; last lines: {' | '.join(tail)[:250]}")
    return None, None, None, notes


def run(gcloud=_gcloud, sleep=time.sleep) -> int:
    region = os.environ.get("GCP_REGION", "us-central1")
    local = json.loads((ru.HERE / "result.json").read_text(encoding="utf-8"))
    try:
        gcloud(["services", "enable", "run.googleapis.com", "logging.googleapis.com"])
        cleanup(region, gcloud)  # leftovers from an interrupted earlier run
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(env_file_text((ru.HERE / "job315.py").read_bytes(), ru.DATA.read_bytes()))
            env_file = f.name
        try:
            how = create_job(region, env_file, gcloud)
        finally:
            os.unlink(env_file)
        print(f"::notice title=Google Cloud day 34::job created on the {ru.RUNTIME_ID} runtime image ({how})")
        cloud, cmd, took, notes = execute(region, gcloud, sleep)
        if cloud is None:
            print(f"::error title=Google Cloud day 34 failed::the job never printed DAY34_RESULT: "
                  f"{' || '.join(notes)[:900]}")
            return 1
    except GcloudError as e:
        print(f"::error title=Google Cloud day 34 failed::{e}")
        return 1
    finally:
        cleanup(region, gcloud)
    errors = compare(cloud, local)
    for e in errors:
        print(f"::error title=Google Cloud day 34 differs from local::{e}")
    if errors:
        return 1
    rt = cloud["runtime"]
    print(f"::notice title=Google Cloud day 34::Cloud Run job on {ru.RUNTIME_ID} (Python {rt['python']} "
          f"{rt['release_level']}, --command={cmd}) matched local: {local['baskets']} baskets, {local['lines']} lines "
          f"at {local['stores']} stores, gross ${local['gross_cents'] / 100:.2f}, promo ${local['discount_cents'] / 100:.2f}, "
          f"net ${local['net_cents'] / 100:.2f}; promo rules {local['promo_rules_raw']} -> "
          f"{local['promo_rules_distinct']} via frozendict; utf8_mode=1, lazy import deferred; {took}s; job deleted")
    return 0
