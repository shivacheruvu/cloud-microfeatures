"""Day 32 (Google Cloud): a Cloud Run job sorts a 6-million-line scan export on an **ephemeral disk** (Cloud Run
release notes 2026-11-04, GA) and rolls it up per SKU, spilling sorted runs to the disk instead of memory.

  python spill_sort.py            # local reference (straight Counter, no sorting), writes result.json
  python spill_sort.py --cloud    # GitHub Actions: create the job with a 1 GiB ephemeral disk, run it, compare, delete
  DAY32_MODE=job python ...       # inside the Cloud Run task (the code arrives base64 in DAY32_CODE)

The job's work is a classic external merge sort: generate the export in 1-million-line chunks, sort each chunk by
SKU and write it as a run file on the disk mount (/mnt/spill), then stream a k-way merge of the run files and roll
the sorted lines up per SKU. The task has 512 MiB of memory; Cloud Run's normal /tmp is in-memory, so ~120 MB of runs
there would eat into that limit. The ephemeral disk keeps them off memory and is wiped when the task ends.

The cloud result must equal the local reference: same rows, runs, spilled bytes, per-SKU checksum, top SKUs.
"""
from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import os
import sys
import time
from pathlib import Path

ROWS, RUN_SIZE = 6_000_000, 1_000_000
STORES, SKUS = 12, 400
MOUNT = "/mnt/spill"
JOB = "cmf-d32-ephemeral-sort"
IMAGE = "docker.io/library/python:3.12-slim"
DISK_SIZE = "1Gi"


def price_cents(idx: int) -> int:
    return 99 + (idx * 37) % 1500


def lines(n: int, start: int = 0):
    """Deterministic scan lines 'sku,store,qty,cents' (a skewed SKU mix: a few SKUs sell a lot)."""
    x = (start * 2654435761 + 12345) & 0xFFFFFFFF
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        idx = ((x % 10007) * (x % 10007) * SKUS) // (10007 * 10007)   # squared: low indexes are common
        store = (x >> 16) % STORES + 1
        qty = (x >> 8) % 4 + 1
        yield f"P{idx:04d},S{store:02d},{qty},{price_cents(idx)}\n"


def _rollup(sorted_lines) -> tuple[dict, int, int]:
    """Roll up SKU-sorted lines: {sku: [units, cents]}, rows, bytes."""
    out, rows, size = {}, 0, 0
    for sku, group in itertools.groupby(sorted_lines, key=lambda s: s[:5]):
        units = cents = 0
        for ln in group:
            _, _, q, c = ln.rstrip("\n").split(",")
            units += int(q)
            cents += int(q) * int(c)
            rows += 1
            size += len(ln)
        out[sku] = [units, cents]
    return out, rows, size


def external_sort(n: int, workdir: str, run_size: int = RUN_SIZE) -> dict:
    """The job: sorted runs spilled to `workdir`, then a streaming k-way merge."""
    gen, runs, spilled = lines(n), [], 0
    while True:
        chunk = list(itertools.islice(gen, run_size))
        if not chunk:
            break
        chunk.sort()
        path = os.path.join(workdir, f"run-{len(runs):03d}.csv")
        with open(path, "w") as f:
            f.writelines(chunk)
        spilled += os.path.getsize(path)
        runs.append(path)
        del chunk
    files = [open(p) for p in runs]
    try:
        per_sku, rows, merged = _rollup(heapq.merge(*files))
    finally:
        for f in files:
            f.close()
        for p in runs:
            os.remove(p)
    return summarise(per_sku, rows, len(runs), spilled, merged)


def reference(n: int = ROWS, run_size: int = RUN_SIZE) -> dict:
    """Local reference: no sorting at all, just a dict over the same lines."""
    per_sku, rows, size = {}, 0, 0
    for ln in lines(n):
        sku, _, q, c = ln.rstrip("\n").split(",")
        a = per_sku.setdefault(sku, [0, 0])
        a[0] += int(q)
        a[1] += int(q) * int(c)
        rows += 1
        size += len(ln)
    return summarise(per_sku, rows, -(-n // run_size), size, size)


def summarise(per_sku: dict, rows: int, runs: int, spilled: int, merged: int) -> dict:
    ordered = sorted(per_sku.items())
    digest = hashlib.sha256("".join(f"{s}:{u}:{c}\n" for s, (u, c) in ordered).encode()).hexdigest()[:16]
    top = sorted(per_sku.items(), key=lambda kv: (-kv[1][0], kv[0]))[:5]
    return {"rows": rows, "runs": runs, "spilled_bytes": spilled, "merged_bytes": merged,
            "distinct_skus": len(per_sku), "units": sum(u for u, _ in per_sku.values()),
            "revenue": round(sum(c for _, c in per_sku.values()) / 100, 2),
            "top_skus": [{"sku": s, "units": u, "revenue": round(c / 100, 2)} for s, (u, c) in top],
            "checksum": digest}


# ---------- inside the Cloud Run task ----------
def disk_facts(mount: str) -> dict:
    st = os.statvfs(mount)
    fstype = next((ln.split()[2] for ln in Path("/proc/mounts").read_text().splitlines()
                   if ln.split()[1] == mount), "unknown")
    mem = "unknown"
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = Path(p).read_text().strip()
            mem = round(int(v) / 2**20) if v.isdigit() else v
            break
        except OSError:
            continue
    return {"mount": mount, "fs_type": fstype, "size_gib": round(st.f_blocks * st.f_frsize / 2**30, 2),
            "memory_limit_mib": mem}


def job_main() -> int:
    import resource
    mount = os.environ.get("DAY32_DISK", MOUNT)
    t0 = time.time()
    facts = disk_facts(mount)
    out = external_sort(ROWS, mount, RUN_SIZE)
    out["disk"] = facts
    # only our run files count: a fresh ext4 disk also holds lost+found (fix forward after the first cloud run)
    out["disk"]["left_after"] = sum(1 for n in os.listdir(mount) if n.startswith("run-"))
    out["disk"]["other_entries"] = sorted(n for n in os.listdir(mount) if not n.startswith("run-"))
    out["seconds"] = round(time.time() - t0, 1)
    out["peak_rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    out["engine"] = f"external merge sort on a Cloud Run ephemeral disk (Python {sys.version.split()[0]})"
    print("DAY32_RESULT " + json.dumps(out, sort_keys=True), flush=True)
    return 0


def main(argv: list[str]) -> int:
    if os.environ.get("DAY32_MODE") == "job":
        return job_main()
    if argv[:1] == ["--cloud"]:
        from cloud_run import run  # noqa: PLC0415 - only needed in GitHub Actions
        return run(Path(__file__).resolve())
    out = {"engine": "pure-Python reference, no sort (local) / external merge sort on a Cloud Run job's ephemeral "
                     "disk (cloud)", "rows_per_run": RUN_SIZE, "disk_size": DISK_SIZE, **reference()}
    (Path(__file__).resolve().parent / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("rows", "runs", "spilled_bytes", "distinct_skus", "units", "revenue",
                                          "checksum")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
