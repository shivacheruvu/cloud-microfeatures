"""Day 12 (Databricks): lane device network audit. Which store's network is each checkout device (registers,
self-checkouts, scales, handheld scanners) really on? Uses the IP address functions new in Databricks SQL 2026.20:
try_ip_host (canonical form, NULL for junk), ip_version and ip_cidr_contains.

  python audit.py --make-data    # regenerate data/subnets.csv and data/devices.csv (deterministic)
  python audit.py --local        # Python's ipaddress module does the same audit; writes result.json
  python audit.py --sql          # print the Databricks statement
  python audit.py --databricks   # serverless SQL warehouse via the Statement Execution API: run the audit and
                                 # compare with local (nothing is created)

The cloud check:
  * errors: any device whose canonical IP, IP version, matched store or status differs from the local audit
Databricks access comes from DATABRICKS_HOST / DATABRICKS_TOKEN (GitHub Actions secrets). Nothing is printed
about the workspace except the query results. All addresses are private (10.0.0.0/8) or documentation
(2001:db8::/32) ranges.
"""
from __future__ import annotations

import csv
import ipaddress
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUBNETS_CSV = HERE / "data" / "subnets.csv"
DEVICES_CSV = HERE / "data" / "devices.csv"
STORES = {"S1": "Downtown Market", "S2": "Riverside Fresh", "S3": "Southgate Grocer", "S4": "Eastfield Foods"}
DEVICE_MIX = [("REG", "register", 4), ("SCO", "self-checkout", 2), ("SCL", "scale", 2), ("HH", "handheld", 2)]
# device_id -> (reported address, what happened); everything else reports a clean address in its own store
PLANTED = {
    "S1-SCL-2": ("10.12.4.62", "scale lent to Eastfield, still registered to Downtown"),
    "S2-HH-1": ("2001:db8:12:3::b1", "handheld roamed onto Southgate's network"),
    "S3-REG-4": ("10.99.0.15", "register on the guest Wi-Fi"),
    "S4-HH-2": ("2001:db8:99::7", "handheld on an unknown IPv6 network"),
    "S2-SCO-2": ("10.12.2.300", "typo in the asset register"),
    "S4-REG-3": ("lane-07", "hostname entered instead of an address"),
}
STATUSES = ("ok", "wrong_store", "unknown_network", "bad_ip")


def make_data() -> tuple[list[dict], list[dict]]:
    subnets, devices = [], []
    for n, sid in enumerate(STORES, start=1):
        subnets += [{"store_id": sid, "cidr": f"10.12.{n}.0/24"}, {"store_id": sid, "cidr": f"2001:db8:12:{n}::/64"}]
        for code, kind, count in DEVICE_MIX:
            for i in range(1, count + 1):
                did = f"{sid}-{code}-{i}"
                if code == "HH":  # handhelds are IPv6; some asset records write it long-hand or in capitals
                    ip = f"2001:db8:12:{n}::{0xa0 + i:x}"
                    if i == 2:
                        ip = ipaddress.ip_address(ip).exploded.upper()
                else:
                    ip = f"10.12.{n}.{ {'REG': 10, 'SCO': 30, 'SCL': 60}[code] + i}"
                devices.append({"device_id": did, "device_type": kind, "home_store": sid,
                                "raw_ip": PLANTED.get(did, (ip,))[0]})
    return subnets, devices


def _read(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load() -> tuple[list[dict], list[dict]]:
    return _read(SUBNETS_CSV), _read(DEVICES_CSV)


def sql_statements(subnets: list[dict], devices: list[dict]) -> dict[str, str]:
    sv = ",\n".join(f"    ('{s['store_id']}', '{s['cidr']}')" for s in subnets)
    dv = ",\n".join(f"    ('{d['device_id']}', '{d['device_type']}', '{d['home_store']}', '{d['raw_ip']}')"
                    for d in devices)
    out = {}
    for chunk in (HERE / "audit.sql").read_text().split(";"):
        lines = chunk.strip().splitlines()
        if not lines:
            continue
        name = lines[0].split(":", 1)[1].strip()
        body = "\n".join(l for l in lines if not l.lstrip().startswith("--"))
        out[name] = body.replace("{subnets}", sv).replace("{devices}", dv)
    return out


# ---------- local: the same audit with Python's ipaddress module
def run_local(subnets: list[dict], devices: list[dict]) -> list[dict]:
    nets = [(s["store_id"], ipaddress.ip_network(s["cidr"])) for s in subnets]
    out = []
    for d in devices:
        try:
            ip = ipaddress.ip_address(d["raw_ip"])
        except ValueError:
            ip = None
        seen = next((sid for sid, net in nets if ip is not None and ip.version == net.version and ip in net), None)
        status = ("bad_ip" if ip is None else "unknown_network" if seen is None
                  else "ok" if seen == d["home_store"] else "wrong_store")
        out.append({"device_id": d["device_id"], "ip": None if ip is None else str(ip),
                    "ip_version": None if ip is None else ip.version, "seen_store": seen, "status": status})
    return sorted(out, key=lambda r: r["device_id"])


def rows_to_result(rows: list[list]) -> list[dict]:
    return [{"device_id": r[0], "ip": r[1], "ip_version": None if r[2] is None else int(r[2]),
             "seen_store": r[3], "status": r[4]} for r in rows]


# ---------- Databricks SQL
def _api(method: str, path: str, body: dict | None = None) -> dict:
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    req = urllib.request.Request(host + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        print(f"::error title=Databricks API {e.code}::{method} {path.split('?')[0]}: {e.read()[:300]!r}")
        raise SystemExit(1)


def _warehouse() -> str:
    warehouses = _api("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
    if not warehouses:
        print("::error title=Databricks day 12::No SQL warehouse in the workspace")
        raise SystemExit(1)
    return next((w for w in warehouses if w.get("enable_serverless_compute")), warehouses[0])["id"]


def statement(wh: str, sql: str) -> list[list]:
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": sql, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(60):  # up to ~5 more minutes while a stopped warehouse starts
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")[:500].replace("\n", " ")
        print(f"::error title=Databricks day 12 {res['status']['state']}::{msg}")
        raise SystemExit(1)
    return res.get("result", {}).get("data_array", []) or []


def run_databricks(subnets: list[dict], devices: list[dict]) -> list[dict]:
    return rows_to_result(statement(_warehouse(), sql_statements(subnets, devices)["databricks"]))


# ---------- comparison and summary
def compare(cloud: list[dict], local: list[dict]) -> list[str]:
    c = {r["device_id"]: r for r in cloud}
    l = {r["device_id"]: r for r in local}
    errors = [f"{k}: local only" for k in l if k not in c] + [f"{k}: cloud only" for k in c if k not in l]
    for k, v in l.items():
        if k in c and c[k] != v:
            diff = {f: [c[k][f], v[f]] for f in v if c[k][f] != v[f]}
            errors.append(f"{k}: cloud vs local {json.dumps(diff)}")
    return errors


def summarise(rows: list[dict], devices: list[dict]) -> dict:
    by_id = {d["device_id"]: d for d in devices}
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    canonicalised = sum(1 for r in rows if r["ip"] is not None and r["ip"] != by_id[r["device_id"]]["raw_ip"])
    flagged = [{"device_id": r["device_id"], "device_type": by_id[r["device_id"]]["device_type"],
                "home_store": by_id[r["device_id"]]["home_store"], "reported": by_id[r["device_id"]]["raw_ip"],
                "seen_store": r["seen_store"], "status": r["status"],
                "why": PLANTED.get(r["device_id"], ("", ""))[1]} for r in rows if r["status"] != "ok"]
    return {
        "stores": len(STORES), "devices": len(rows),
        "ipv4": sum(1 for r in rows if r["ip_version"] == 4), "ipv6": sum(1 for r in rows if r["ip_version"] == 6),
        **counts, "canonicalised": canonicalised, "flagged": flagged,
        "headline": f"{counts['ok']} of {len(rows)} devices on their own store's network; "
                    f"{counts['wrong_store']} in the wrong store, {counts['unknown_network']} on an unknown network, "
                    f"{counts['bad_ip']} bad addresses; {canonicalised} IPv6 addresses cleaned to canonical form",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--make-data":
        subnets, devices = make_data()
        for path, rows in ((SUBNETS_CSV, subnets), (DEVICES_CSV, devices)):
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        raise SystemExit(0)
    subnets, devices = load()
    if mode == "--sql":
        print(sql_statements(subnets, devices)["databricks"] + ";")
    elif mode == "--databricks":
        local = run_local(subnets, devices)
        cloud = run_databricks(subnets, devices)
        errors = compare(cloud, local)
        if errors:
            print(f"::error title=Databricks result differs from local::{len(errors)} difference(s): "
                  f"{json.dumps(errors)[:850]}")
            raise SystemExit(1)
        s = summarise(cloud, devices)
        flagged = {f["device_id"]: f"{f['status']}" + (f" ({f['seen_store']})" if f["seen_store"] else "")
                   for f in s["flagged"]}
        print("::notice title=Databricks day 12::try_ip_host + ip_version + ip_cidr_contains ran on a serverless SQL "
              f"warehouse and matched the local ipaddress audit for all {s['devices']} devices: {s['headline']}; "
              f"flagged {json.dumps(flagged)}")
    else:
        summary = summarise(run_local(subnets, devices), devices)
        (HERE / "result.json").write_text(json.dumps({"engine": "Python ipaddress (local) / Databricks SQL "
                                                      "try_ip_host + ip_cidr_contains (cloud)", **summary},
                                                     indent=2) + "\n")
        print(json.dumps(summary, indent=2))
