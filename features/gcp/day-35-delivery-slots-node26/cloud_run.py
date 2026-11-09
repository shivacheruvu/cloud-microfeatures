"""Day 35 (Google Cloud) cloud part: create a Cloud Run job on Google's nodejs26 runtime base image, execute it once,
read its DAY35_RESULT log line, compare with the local reference (result.json); delete the job on exit.

gcloud only (keyless sign-in in GitHub Actions). cloud.sh redacts the project ID, accounts and long numbers.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time

import slots as sl

LOG_WAIT_SECONDS, LOG_POLL_SECONDS = 240, 15
# node -e '<this>': decode and run job26.js (ASCII) from the env var. No commas: gcloud splits --args on them.
COMMAND_ARG = "eval(atob(process.env.DAY35_CODE))"
# Must be built in: the run fails without them. Temporal and Math.sumPrecise are used when present and reported.
REQUIRED = ("RegExp.escape", "Uint8Array.fromBase64")
# node is on PATH in the image (first cloud run, 2026-11-08). Node.js 26 has no Temporal global by default, so the
# job first runs with V8's --harmony-temporal flag; if node rejects the flag, it runs without it (the job then uses
# its Intl.DateTimeFormat path, which gives the same results). Each image stack is tried in turn.
TEMPORAL_FLAG = "--harmony-temporal"
ATTEMPTS = tuple((image, flags) for image in sl.IMAGES for flags in ((TEMPORAL_FLAG,), ()))


class GcloudError(Exception):
    pass


def _gcloud(args: list[str], timeout: int = 900) -> str:
    p = subprocess.run(["gcloud", *args, "--quiet"], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        err = " ".join((p.stderr or "").split())
        raise GcloudError(f"gcloud {' '.join(a for a in args[:3] if not a.startswith('-'))}: {err[-500:]}")
    return p.stdout


def node_args(flags: tuple = (TEMPORAL_FLAG,)) -> str:
    return "--args=" + ",".join([*flags, "-e", COMMAND_ARG])


def create_args(region: str, env_file: str, image: str = sl.IMAGES[0], flags: tuple = (TEMPORAL_FLAG,),
                service_account: str | None = None) -> list[str]:
    args = ["run", "jobs", "create", sl.JOB, f"--region={region}", f"--image={image}",
            "--command=node", node_args(flags), f"--env-vars-file={env_file}",
            "--cpu=1", "--memory=512Mi", "--tasks=1", "--max-retries=0", "--task-timeout=300s",
            "--labels=app=cloud-microfeatures,day=35"]
    if service_account:
        args.append(f"--service-account={service_account}")
    return args


def env_file_text(code: bytes, data: bytes) -> str:
    return (f"DAY35_CODE: \"{base64.b64encode(code).decode()}\"\n"
            f"DAY35_DATA: \"{base64.b64encode(data).decode()}\"\n")


def log_filter(execution: str) -> str:
    return (f'resource.type="cloud_run_job" AND resource.labels.job_name="{sl.JOB}" AND '
            f'labels."run.googleapis.com/execution_name"="{execution}"')


def read_result(execution: str, gcloud=_gcloud, sleep=time.sleep, wait: int = LOG_WAIT_SECONDS):
    """Wait for the task's DAY35_RESULT line in Cloud Logging. Returns (result, last log lines)."""
    deadline, tail = time.time() + wait, []
    while True:
        raw = gcloud(["logging", "read", log_filter(execution), "--freshness=2h", "--limit=50", "--format=json"])
        texts = [e.get("textPayload") or "" for e in json.loads(raw or "[]")]
        for t in texts:
            if t.startswith("DAY35_RESULT "):
                return json.loads(t[len("DAY35_RESULT "):]), texts
        tail = [t for t in texts if t][:5]
        if time.time() >= deadline:
            return None, tail
        sleep(LOG_POLL_SECONDS)


def compare(cloud: dict, local: dict) -> list[str]:
    errors = [f"{k}: cloud {json.dumps(cloud.get(k), ensure_ascii=False)[:300]} vs local "
              f"{json.dumps(local[k], ensure_ascii=False)[:300]}" for k in sl.KEYS if cloud.get(k) != local[k]]
    rt = cloud.get("runtime") or {}
    if str(rt.get("node", "")).split(".")[0] != "26":
        errors.append(f"job ran Node.js {rt.get('node')}, expected 26.x from the {sl.RUNTIME_ID} image")
    feats = rt.get("features") or {}
    missing = [k for k in REQUIRED if feats.get(k) != "function"]
    if missing:
        errors.append(f"not available in this Node.js: {', '.join(missing)}")
    if rt.get("engines_agree") is False:
        errors.append("Temporal and Intl.DateTimeFormat gave different delivery times")
    for name, msg in (rt.get("errors") or {}).items():
        errors.append(f"{name} threw: {msg}")
    return errors


def cleanup(region: str, gcloud=_gcloud) -> None:
    try:
        names = gcloud(["run", "jobs", "list", f"--region={region}", "--filter=metadata.name~^cmf-d35-",
                        "--format=value(metadata.name)"]).split()
    except GcloudError as e:
        print(f"::warning title=Google Cloud day 35 cleanup::couldn't list jobs: {e}")
        return
    for n in names:
        try:
            gcloud(["run", "jobs", "delete", n, f"--region={region}"])
        except GcloudError as e:
            print(f"::warning title=Google Cloud day 35 cleanup::couldn't delete job {n}: {e}")


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
    """Execute the job; if the task fails (unknown V8 flag, image stack), switch to the next (image, flags) and try
    again. Returns (result, image, flags, seconds, notes)."""
    notes = []
    for i, (image, flags) in enumerate(ATTEMPTS):
        if i:
            gcloud(["run", "jobs", "update", sl.JOB, f"--region={region}", f"--image={image}", node_args(flags)])
        t0 = time.time()
        stack = image.split("/")[-3]
        cmd = " ".join(["node", *flags])
        try:
            execution = gcloud(["run", "jobs", "execute", sl.JOB, f"--region={region}", "--wait",
                                "--format=value(metadata.name)"], timeout=900).strip()
        except GcloudError as e:
            notes.append(f"{stack} {cmd}: {str(e)[-250:]}")
            continue
        cloud, tail = read_result(execution, gcloud, sleep, wait)
        if cloud is not None:
            return cloud, image, cmd, round(time.time() - t0), notes
        notes.append(f"{stack} {cmd}: no DAY35_RESULT line; last lines: {' | '.join(tail)[:250]}")
    return None, None, None, None, notes


def run(gcloud=_gcloud, sleep=time.sleep) -> int:
    region = os.environ.get("GCP_REGION", "us-central1")
    local = json.loads((sl.HERE / "result.json").read_text(encoding="utf-8"))
    try:
        gcloud(["services", "enable", "run.googleapis.com", "logging.googleapis.com"])
        cleanup(region, gcloud)  # leftovers from an interrupted earlier run
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(env_file_text((sl.HERE / "job26.js").read_bytes(), sl.DATA.read_bytes()))
            env_file = f.name
        try:
            how = create_job(region, env_file, gcloud)
        finally:
            os.unlink(env_file)
        print(f"::notice title=Google Cloud day 35::job created on the {sl.RUNTIME_ID} runtime image ({how})")
        cloud, image, cmd, took, notes = execute(region, gcloud, sleep)
        if cloud is None:
            print(f"::error title=Google Cloud day 35 failed::the job never printed DAY35_RESULT: "
                  f"{' || '.join(notes)[:900]}")
            return 1
    except GcloudError as e:
        print(f"::error title=Google Cloud day 35 failed::{e}")
        return 1
    finally:
        cleanup(region, gcloud)
    errors = compare(cloud, local)
    for e in errors:
        print(f"::error title=Google Cloud day 35 differs from local::{e}")
    if errors:
        return 1
    rt = cloud["runtime"]
    s1 = local["stores"]["S1"]
    print(f"::notice title=Google Cloud day 35::Cloud Run job on {sl.RUNTIME_ID} ({image.split('/')[-3]}, Node.js "
          f"{rt['node']}, V8 {rt['v8']}, `{cmd}`; time zones via {rt.get('tz_engine')}, total via "
          f"{rt.get('sum_engine')}; Temporal with the flag: {rt.get('temporal_with_harmony_flag')}) matched local: {len(local['orders'])} orders at "
          f"{len(local['stores'])} stores; Nov 1 had {s1['hours_in_day']:g} h in Chicago; "
          f"{local['late_if_naive']} promises an hour off with wall-clock maths, "
          f"{local['deliveries_in_repeated_hour']} due in the repeated 01:00 hour; sumPrecise "
          f"{local['total_precise']:.2f} exactly (a plain reduce is off by "
          f"{abs(local['total_naive'] - local['total_precise']):.1e}); {took}s; job deleted")
    return 0
