"""Day 32 (Google Cloud) cloud part: create a Cloud Run job with a 1 GiB ephemeral disk, execute it once, read its
DAY32_RESULT log line and compare with the local reference (result.json); delete the job on exit.

Uses gcloud only (keyless sign-in in GitHub Actions). Never prints the project ID, the account or the region's
resource names beyond the job name; cloud.sh also redacts the project ID and long numbers.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import spill_sort as ss

LOG_WAIT_SECONDS, LOG_POLL_SECONDS = 240, 15
COMMAND_ARG = "exec(__import__('base64').b64decode(__import__('os').environ.get('DAY32_CODE')))"  # no commas


class GcloudError(Exception):
    pass


def _gcloud(args: list[str], timeout: int = 900) -> str:
    p = subprocess.run(["gcloud", *args, "--quiet"], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        err = " ".join((p.stderr or "").split())
        raise GcloudError(f"gcloud {' '.join(a for a in args[:3] if not a.startswith('-'))}: {err[-500:]}")
    return p.stdout


def create_args(region: str, env_file: str, beta: bool = False, service_account: str | None = None) -> list[str]:
    args = (["beta"] if beta else []) + [
        "run", "jobs", "create", ss.JOB, f"--region={region}", f"--image={ss.IMAGE}",
        "--command=python3", f"--args=-c,{COMMAND_ARG}", f"--env-vars-file={env_file}",
        "--cpu=1", "--memory=512Mi", "--tasks=1", "--max-retries=0", "--task-timeout=900s",
        f"--add-volume=name=spill,type=ephemeral-disk,size={ss.DISK_SIZE}",
        f"--add-volume-mount=volume=spill,mount-path={ss.MOUNT}",
        "--labels=app=cloud-microfeatures,day=32"]
    if service_account:
        args.append(f"--service-account={service_account}")
    return args


def env_file_text(code: bytes) -> str:
    return (f"DAY32_MODE: job\nDAY32_DISK: {ss.MOUNT}\n"
            f"DAY32_CODE: \"{base64.b64encode(code).decode()}\"\n")


def log_filter(execution: str) -> str:
    return (f'resource.type="cloud_run_job" AND resource.labels.job_name="{ss.JOB}" AND '
            f'labels."run.googleapis.com/execution_name"="{execution}"')


def read_result(execution: str, gcloud=_gcloud, sleep=time.sleep) -> tuple[dict | None, list[str]]:
    """Wait for the task's DAY32_RESULT line in Cloud Logging. Returns (result, last log lines)."""
    deadline, tail = time.time() + LOG_WAIT_SECONDS, []
    while True:
        raw = gcloud(["logging", "read", log_filter(execution), "--freshness=2h", "--limit=50", "--format=json"])
        entries = json.loads(raw or "[]")
        texts = [e.get("textPayload") or "" for e in entries]
        for t in texts:
            if t.startswith("DAY32_RESULT "):
                return json.loads(t[len("DAY32_RESULT "):]), texts
        tail = [t for t in texts if t][:5]
        if time.time() >= deadline:
            return None, tail
        sleep(LOG_POLL_SECONDS)


def compare(cloud: dict, local: dict) -> list[str]:
    errors = [f"{k}: cloud {json.dumps(cloud.get(k))[:300]} vs local {json.dumps(local[k])[:300]}"
              for k in ("rows", "runs", "spilled_bytes", "merged_bytes", "distinct_skus", "units", "revenue",
                        "top_skus", "checksum") if cloud.get(k) != local[k]]
    disk = cloud.get("disk") or {}
    if disk.get("fs_type") != "ext4":
        errors.append(f"spill mount isn't the ext4 ephemeral disk: {json.dumps(disk)}")
    if not 0.9 <= float(disk.get("size_gib") or 0) <= 1.05:
        errors.append(f"spill mount size {disk.get('size_gib')} GiB, expected about 1 GiB")
    if disk.get("left_after") != 0:
        errors.append(f"{disk.get('left_after')} run files left on the disk")
    return errors


def cleanup(region: str, gcloud=_gcloud) -> None:
    try:
        names = gcloud(["run", "jobs", "list", f"--region={region}", "--filter=metadata.name~^cmf-d32-",
                        "--format=value(metadata.name)"]).split()
    except GcloudError as e:
        print(f"::warning title=Google Cloud day 32 cleanup::couldn't list jobs: {e}")
        return
    for n in names:
        try:
            gcloud(["run", "jobs", "delete", n, f"--region={region}"])
        except GcloudError as e:
            print(f"::warning title=Google Cloud day 32 cleanup::couldn't delete job {n}: {e}")


def create_job(region: str, env_file: str, gcloud=_gcloud) -> str:
    """GA command first; fall back to the beta command (older gcloud) and then to the signed-in account as the
    job's identity (if the default compute service account can't be used). Returns how it was created."""
    attempts = [("gcloud run jobs create", {}), ("gcloud beta run jobs create", {"beta": True})]
    errors = []
    for label, kw in attempts:
        try:
            gcloud(create_args(region, env_file, **kw))
            return label
        except GcloudError as e:
            errors.append(str(e))
            msg = str(e).lower()
            if "service account" in msg or "iam.serviceaccounts.actas" in msg:
                account = gcloud(["config", "get-value", "account"]).strip()
                gcloud(create_args(region, env_file, service_account=account, **kw))
                return label + " (signed-in service account as job identity)"
    raise GcloudError(" | ".join(errors))


def run(script: Path, gcloud=_gcloud, sleep=time.sleep) -> int:
    region = os.environ.get("GCP_REGION", "us-central1")
    local = json.loads((script.parent / "result.json").read_text())
    try:
        gcloud(["services", "enable", "run.googleapis.com", "logging.googleapis.com"])
        cleanup(region, gcloud)  # leftovers from an interrupted earlier run
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(env_file_text(script.read_bytes()))
            env_file = f.name
        try:
            how = create_job(region, env_file, gcloud)
        finally:
            os.unlink(env_file)
        print(f"::notice title=Google Cloud day 32::job created with a {ss.DISK_SIZE} ephemeral disk ({how})")
        t0 = time.time()
        execution = gcloud(["run", "jobs", "execute", ss.JOB, f"--region={region}", "--wait",
                            "--format=value(metadata.name)"], timeout=1200).strip()
        took = round(time.time() - t0)
        cloud, tail = read_result(execution, gcloud, sleep)
        if cloud is None:
            print(f"::error title=Google Cloud day 32 failed::the execution finished but no DAY32_RESULT line reached "
                  f"Cloud Logging; last lines: {' | '.join(tail)[:600]}")
            return 1
    except GcloudError as e:
        print(f"::error title=Google Cloud day 32 failed::{e}")
        return 1
    finally:
        cleanup(region, gcloud)
    errors = compare(cloud, local)
    for e in errors:
        print(f"::error title=Google Cloud day 32 differs from local::{e}")
    if errors:
        return 1
    d = cloud["disk"]
    print(f"::notice title=Google Cloud day 32::Cloud Run job matched local: {local['rows']:,} scan lines sorted in "
          f"{local['runs']} runs spilled to a {d['size_gib']} GiB {d['fs_type']} ephemeral disk "
          f"({local['spilled_bytes'] / 1e6:.1f} MB, peak RSS {cloud.get('peak_rss_mib')} MiB of "
          f"{d.get('memory_limit_mib')} MiB), {local['distinct_skus']} SKUs, {local['units']:,} units, "
          f"checksum {local['checksum']}; sort {cloud.get('seconds')}s in the task, {took}s end to end; job deleted")
    return 0
