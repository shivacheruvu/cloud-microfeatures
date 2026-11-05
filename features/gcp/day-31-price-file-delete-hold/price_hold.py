"""Day 31 (Google Cloud): an incident hold on the price-file bucket with Cloud Storage's new **hard delete pause**
(Google Cloud release notes, 2026-11-04): while `softDeletePolicy.hardDeletePause.enabled` is true, soft-deleted
objects are not permanently deleted, however old they get, until the pause is lifted.

  python price_hold.py            # local: build the files and the restore plan, write result.json
  python price_hold.py --cloud    # GitHub Actions: run the incident on a short-lived bucket (JSON API)

The story: each store drops a daily shelf-price CSV into a raw bucket with 7-day soft delete. A bad cleanup job
deletes four good price files (all of Monday plus east's Tuesday file) and one draft file that should be gone. Ops
pauses hard deletion at once, so nothing can age out while the incident is open, restores exactly the four good
files from their soft-deleted generations, leaves the draft soft-deleted, then lifts the pause.

The cloud run checks every step through the Cloud Storage JSON API: the pause reads back as enabled with an
effective time, the soft-deleted listing is exactly the five deleted files, a soft-deleted file can't be read, each
restored file has the original MD5, the live listing equals the local plan, and the pause reads back as disabled.
The bucket is deleted at the end (soft delete cleared first, so it doesn't linger as a soft-deleted bucket).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORES = ["east", "west", "north"]
DAYS = ["2026-11-02", "2026-11-03"]
SKUS = [("0001", "bananas 1 lb", 0.69), ("0002", "whole milk 1 gal", 3.89), ("0003", "sourdough loaf", 4.99),
        ("0004", "eggs dozen", 3.29), ("0005", "baby spinach 5 oz", 3.49), ("0006", "cheddar 8 oz", 4.29)]
RETENTION_SECONDS = 7 * 24 * 3600     # the soft delete minimum (7 days)
PREFIX = "cmf-d31-"
API = "https://storage.googleapis.com/storage/v1"
UPLOAD = "https://storage.googleapis.com/upload/storage/v1"


def price_csv(store: str, day: str, draft: bool = False) -> bytes:
    """One store's shelf prices for one day. Store and day shift prices a little so every file differs."""
    si, di = STORES.index(store), DAYS.index(day)
    lines = ["sku,product,shelf_price"]
    for i, (sku, name, base) in enumerate(SKUS):
        price = round(base * (1 + 0.02 * si) + 0.10 * ((i + di) % 2), 2)
        if draft:
            price = round(price * 0.5, 2)   # the half-price draft nobody approved
        lines.append(f"{sku},{name},{price:.2f}")
    return ("\n".join(lines) + "\n").encode()


def files() -> dict[str, bytes]:
    out = {f"prices/{day}/{store}.csv": price_csv(store, day) for day in DAYS for store in STORES}
    out[f"prices/{DAYS[1]}/west-draft.csv"] = price_csv("west", DAYS[1], draft=True)
    return out


def bad_cleanup() -> list[str]:
    """What the broken cleanup job deleted: all of Monday, east's Tuesday file, and the draft (rightly)."""
    return sorted([f"prices/{DAYS[0]}/{s}.csv" for s in STORES]
                  + [f"prices/{DAYS[1]}/east.csv", f"prices/{DAYS[1]}/west-draft.csv"])


def restore_plan(deleted: list[str]) -> tuple[list[str], list[str]]:
    """Restore every deleted price file except drafts; drafts stay soft-deleted."""
    keep_deleted = [n for n in deleted if n.endswith("-draft.csv")]
    return [n for n in deleted if n not in keep_deleted], keep_deleted


def md5_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data).digest()).decode()   # the format of the JSON API md5Hash field


def summarise() -> dict:
    f = files()
    deleted = bad_cleanup()
    restore, left = restore_plan(deleted)
    live_after = sorted(n for n in f if n not in left)
    return {
        "files_uploaded": len(f), "bytes_uploaded": sum(len(v) for v in f.values()),
        "deleted_by_cleanup": deleted, "restored": restore, "left_soft_deleted": left,
        "live_before_restore": sorted(n for n in f if n not in deleted), "live_after": live_after,
        "price_rows_recovered": sum(len(f[n].decode().splitlines()) - 1 for n in restore),
        "retention_days": RETENTION_SECONDS // 86400,
        "md5": {n: md5_b64(f[n]) for n in restore},
    }


# ---------------------------------------------------------------- cloud (JSON API with the runner's gcloud token)
class GcsError(Exception):
    pass


def _token() -> str:
    return subprocess.run(["gcloud", "auth", "print-access-token"], check=True, capture_output=True,
                          text=True).stdout.strip()


def http(method: str, url: str, body: bytes | None = None, ctype: str = "application/json") -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Authorization": f"Bearer {_token()}", "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Gcs:
    """The handful of JSON API calls this day needs. `transport` is swapped for a fake in the local test."""

    def __init__(self, project: str, transport=None):
        self.project, self.t = project, transport or http

    def call(self, method, url, body=None, ok=(200,), ctype="application/json", raw=False):
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        s, out = self.t(method, url, data, ctype)
        if s not in ok:
            raise GcsError(f"{method} {url.split('?')[0].split('/storage/v1/')[-1]} -> HTTP {s}: "
                           f"{out[:500].decode(errors='replace')}")
        if raw:
            return s, out
        return json.loads(out) if out and out.strip() else {}

    @staticmethod
    def o(name: str) -> str:
        return urllib.parse.quote(name, safe="")

    def create_bucket(self, bucket: str) -> dict:
        return self.call("POST", f"{API}/b?project={urllib.parse.quote(self.project)}", {
            "name": bucket, "location": "US",
            "iamConfiguration": {"uniformBucketLevelAccess": {"enabled": True}, "publicAccessPrevention": "enforced"},
            "softDeletePolicy": {"retentionDurationSeconds": str(RETENTION_SECONDS)},
            "labels": {"app": "cloud-microfeatures", "day": "31"}})

    def set_pause(self, bucket: str, enabled: bool, retention: int = RETENTION_SECONDS) -> dict:
        return self.call("PATCH", f"{API}/b/{bucket}?fields=softDeletePolicy", {"softDeletePolicy": {
            "retentionDurationSeconds": str(retention), "hardDeletePause": {"enabled": enabled}}})

    def clear_soft_delete(self, bucket: str) -> dict:
        return self.call("PATCH", f"{API}/b/{bucket}?fields=softDeletePolicy",
                         {"softDeletePolicy": {"retentionDurationSeconds": "0"}})

    def upload(self, bucket: str, name: str, data: bytes) -> dict:
        return self.call("POST", f"{UPLOAD}/b/{bucket}/o?uploadType=media&name={self.o(name)}", data,
                         ctype="text/csv")

    def delete(self, bucket: str, name: str, ok=(200, 204)) -> None:
        self.call("DELETE", f"{API}/b/{bucket}/o/{self.o(name)}", ok=ok)

    def list(self, bucket: str, soft_deleted: bool = False) -> list[dict]:
        items, token = [], ""
        while True:
            q = "&softDeleted=true" if soft_deleted else ""
            r = self.call("GET", f"{API}/b/{bucket}/o?maxResults=1000{q}" + (f"&pageToken={token}" if token else ""))
            items += r.get("items") or []
            token = r.get("nextPageToken")
            if not token:
                return items

    def read_status(self, bucket: str, name: str) -> int:
        s, _ = self.call("GET", f"{API}/b/{bucket}/o/{self.o(name)}?alt=media", ok=(200, 403, 404), raw=True)
        return s

    def restore(self, bucket: str, name: str, generation: str) -> dict:
        return self.call("POST", f"{API}/b/{bucket}/o/{self.o(name)}/restore?generation={generation}")

    def delete_bucket(self, bucket: str) -> None:
        self.call("DELETE", f"{API}/b/{bucket}", ok=(200, 204, 404))

    def buckets(self, prefix: str) -> list[str]:
        r = self.call("GET", f"{API}/b?project={urllib.parse.quote(self.project)}&prefix={prefix}&maxResults=100")
        return [b["name"] for b in r.get("items") or []]


def pause_of(resp: dict) -> dict:
    return (resp.get("softDeletePolicy") or {}).get("hardDeletePause") or {}


def teardown(g: Gcs, bucket: str) -> None:
    """Lift the pause, clear soft delete, bring back anything still soft-deleted so it can be deleted for good, delete
    every live object, delete the bucket. Never raises."""
    for step in (lambda: g.set_pause(bucket, False), lambda: g.clear_soft_delete(bucket)):
        try:
            step()
        except GcsError:
            pass
    try:
        for _ in range(3):
            for it in g.list(bucket, soft_deleted=True):
                try:
                    g.restore(bucket, it["name"], it["generation"])
                except GcsError:
                    pass     # e.g. a live object with that name exists; deleting it below with soft delete off is final
            for it in g.list(bucket):
                g.delete(bucket, it["name"], ok=(200, 204, 404))
            if not g.list(bucket, soft_deleted=True):
                break
        g.delete_bucket(bucket)
    except GcsError as e:
        print(f"::warning title=Google Cloud day 31 cleanup::{redact(str(e), bucket)}")


def incident(g: Gcs, bucket: str, ref: dict) -> dict:
    """Run the incident end to end and return what the cloud saw (compared with `ref` by `compare`)."""
    f = files()
    g.create_bucket(bucket)
    uploaded = {n: g.upload(bucket, n, data) for n, data in sorted(f.items())}
    md5_mismatch = [n for n, o in uploaded.items() if o.get("md5Hash") != md5_b64(f[n])]
    for n in ref["deleted_by_cleanup"]:
        g.delete(bucket, n)
    live_before = sorted(o["name"] for o in g.list(bucket))

    paused = g.set_pause(bucket, True)          # the new part
    soft = {o["name"]: o for o in g.list(bucket, soft_deleted=True)}
    blocked_read = {n: g.read_status(bucket, n) for n in ref["restored"][:1]}
    restored = {n: g.restore(bucket, n, soft[n]["generation"]) for n in ref["restored"] if n in soft}
    live_after = sorted(o["name"] for o in g.list(bucket))
    # soft-deleted files that are still not back (restore makes a new live generation; whether the old soft-deleted
    # generation stays listed doesn't matter for the incident, so it's recorded, not checked)
    soft_after = [o["name"] for o in g.list(bucket, soft_deleted=True)]
    still_soft = sorted(set(soft_after) - set(live_after))
    lifted = g.set_pause(bucket, False)
    return {
        "md5_mismatch_on_upload": md5_mismatch,
        "live_before_restore": live_before,
        "pause_enabled": pause_of(paused).get("enabled") is True,
        "pause_effective_time": bool(pause_of(paused).get("effectiveTime")),
        "soft_deleted": sorted(soft),
        "hard_delete_times": all(bool(o.get("hardDeleteTime")) for o in soft.values()),
        "soft_deleted_read_status": list(blocked_read.values()),
        "restored_md5": {n: o.get("md5Hash") for n, o in restored.items()},
        "live_after": live_after,
        "left_soft_deleted": still_soft,
        "soft_deleted_listed_after_restore": len(soft_after),
        "pause_lifted": pause_of(lifted).get("enabled") is not True,
    }


def compare(cloud: dict, ref: dict) -> list[str]:
    errors = []
    if cloud["md5_mismatch_on_upload"]:
        errors.append(f"upload MD5 differs for {cloud['md5_mismatch_on_upload']}")
    if not cloud["pause_enabled"]:
        errors.append("hardDeletePause didn't read back as enabled after the PATCH")
    if not cloud["pause_effective_time"]:
        errors.append("hardDeletePause has no effectiveTime")
    for k in ("live_before_restore", "live_after", "left_soft_deleted"):
        if cloud[k] != ref[k]:
            errors.append(f"{k}: cloud {cloud[k]} vs plan {ref[k]}")
    if cloud["soft_deleted"] != ref["deleted_by_cleanup"]:
        errors.append(f"soft-deleted listing {cloud['soft_deleted']} vs deleted {ref['deleted_by_cleanup']}")
    if not cloud["hard_delete_times"]:
        errors.append("a soft-deleted object has no hardDeleteTime")
    if any(s == 200 for s in cloud["soft_deleted_read_status"]):
        errors.append("a soft-deleted object could still be read")
    if cloud["restored_md5"] != ref["md5"]:
        errors.append(f"restored MD5s {cloud['restored_md5']} vs originals {ref['md5']}")
    if not cloud["pause_lifted"]:
        errors.append("hardDeletePause still enabled after lifting it")
    return errors


def redact(text: str, bucket: str = "") -> str:
    p = os.environ.get("GCP_PROJECT_ID", "")
    if p:
        text = text.replace(p, "<project>")
    if bucket:
        text = text.replace(bucket, "<bucket>")
    text = re.sub(PREFIX + r"[a-z0-9-]+", "<bucket>", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<account>", text)
    return re.sub(r"\b\d{6,}\b", "<number>", text)


def cloud_main(g: Gcs, run_id: str) -> int:
    ref = json.loads((HERE / "result.json").read_text())
    for old in g.buckets(PREFIX):            # leftovers from an interrupted earlier run
        teardown(g, old)
        print("::notice title=Google Cloud day 31 cleanup::deleted one leftover bucket")
    bucket = f"{PREFIX}{run_id}-{hashlib.sha1(run_id.encode()).hexdigest()[:6]}"
    try:
        cloud = incident(g, bucket, ref)
    except GcsError as e:
        print(f"::error title=Google Cloud day 31 failed::{redact(str(e), bucket)}")
        return 1
    finally:
        teardown(g, bucket)
    errors = compare(cloud, ref)
    for e in errors:
        print(f"::error title=Google Cloud day 31 differs from plan::{redact(e, bucket)}")
    if errors:
        return 1
    print(f"::notice title=Google Cloud day 31::hard delete pause read back enabled with an effective time; "
          f"{len(ref['deleted_by_cleanup'])} files soft-deleted by the bad cleanup, {len(ref['restored'])} restored "
          f"with their original MD5 ({ref['price_rows_recovered']} price rows), the draft left soft-deleted, "
          f"soft-deleted reads refused (HTTP {cloud['soft_deleted_read_status'][0]}), pause lifted; "
          f"live files {len(cloud['live_after'])} = plan; bucket deleted")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--cloud"]:
        return cloud_main(Gcs(os.environ["GCP_PROJECT_ID"]), os.environ.get("GITHUB_RUN_ID", "local").lower())
    out = {"engine": "local plan (pure Python) / Cloud Storage JSON API with softDeletePolicy.hardDeletePause (cloud)",
           **summarise()}
    (HERE / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("files_uploaded", "restored", "left_soft_deleted", "price_rows_recovered")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
