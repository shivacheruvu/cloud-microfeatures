"""Day 31 Google Cloud: the price-file incident plan (what the bad cleanup deleted, what to restore, what stays
soft-deleted) is current in result.json, and the cloud runner drives the hard delete pause, the soft-deleted listing,
restores and teardown correctly against a fake Cloud Storage JSON API (no credentials). The fake keeps soft-deleted
objects while a pause is on and refuses reads of soft-deleted objects, as the real service does."""
import importlib.util
import json
import urllib.parse
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/gcp/day-31-price-file-delete-hold"
spec = importlib.util.spec_from_file_location("day31_price_hold", FEATURE / "price_hold.py")
ph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ph)
RESULT = json.loads((FEATURE / "result.json").read_text())


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == json.loads(json.dumps(ph.summarise()))


def test_plan_headlines():
    assert RESULT["files_uploaded"] == 7 and len(RESULT["deleted_by_cleanup"]) == 5
    assert len(RESULT["restored"]) == 4 and RESULT["left_soft_deleted"] == ["prices/2026-11-03/west-draft.csv"]
    assert RESULT["price_rows_recovered"] == 24 and len(RESULT["live_after"]) == 6
    assert RESULT["retention_days"] == 7
    assert len(set(ph.files().values())) == 7          # every file differs, so MD5 checks mean something


def test_md5_matches_the_json_api_format():
    assert ph.md5_b64(b"") == "1B2M2Y8AsgTpgAmY7PhCfg=="


class FakeGcs:
    """In-memory buckets with live objects, soft-deleted generations, soft delete policy and hard delete pause."""

    def __init__(self, pause_supported=True):
        self.buckets, self.gen, self.calls, self.pause_supported = {}, 100, [], pause_supported

    def __call__(self, method, url, body, ctype):
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        parts = u.path.split("/")
        self.calls.append((method, u.path, q))
        path = u.path.split("/storage/v1/")[1]
        seg = path.split("/")
        if path == "b" and method == "POST":
            b = json.loads(body)
            self.buckets[b["name"]] = {"live": {}, "soft": [], "policy": dict(b["softDeletePolicy"])}
            return 200, json.dumps({"name": b["name"], "softDeletePolicy": b["softDeletePolicy"]}).encode()
        if path == "b" and method == "GET":
            return 200, json.dumps({"items": [{"name": n} for n in self.buckets if n.startswith(q["prefix"])]}).encode()
        bk = self.buckets.get(seg[1])
        if bk is None:
            return 404, b'{"error": "no bucket"}'
        if len(seg) == 2:
            if method == "DELETE":
                if bk["live"]:
                    return 409, b'{"error": "bucket not empty"}'
                del self.buckets[seg[1]]
                return 204, b""
            if method == "PATCH":
                p = json.loads(body)["softDeletePolicy"]
                if "hardDeletePause" in p and not self.pause_supported:
                    return 400, b'{"error": "Invalid argument"}'
                pol = {"retentionDurationSeconds": p["retentionDurationSeconds"]}
                if p.get("hardDeletePause", {}).get("enabled"):
                    pol["hardDeletePause"] = {"enabled": True, "effectiveTime": "2026-11-05T10:00:00Z"}
                bk["policy"] = pol
                return 200, json.dumps({"softDeletePolicy": pol}).encode()
        if seg[2] == "o" and len(seg) == 3:
            if method == "POST":                          # media upload
                return 200, json.dumps(self._put(bk, q["name"], body)).encode()
            if q.get("softDeleted") == "true":
                items = [{"name": n, "generation": g, "softDeleteTime": "t", "hardDeleteTime": "t+7d"}
                         for n, g, _ in bk["soft"]]
            else:
                items = [{"name": n, "md5Hash": o["md5Hash"]} for n, o in sorted(bk["live"].items())]
            return 200, json.dumps({"items": items}).encode()
        name = urllib.parse.unquote(seg[3])
        if method == "DELETE":
            o = bk["live"].pop(name, None)
            if o is None:
                return 404, b"{}"
            if int(bk["policy"]["retentionDurationSeconds"]) > 0:
                bk["soft"].append((name, o["generation"], o["data"]))
            return 204, b""
        if method == "GET":                                # alt=media
            return (200, bk["live"][name]["data"]) if name in bk["live"] else (404, b"{}")
        if len(seg) == 5 and seg[4] == "restore":
            if name in bk["live"]:
                return 412, b'{"error": "live object exists"}'
            hit = [s for s in bk["soft"] if s[0] == name and s[1] == q["generation"]]
            if not hit:
                return 404, b"{}"
            bk["soft"].remove(hit[0])
            return 200, json.dumps(self._put(bk, name, hit[0][2])).encode()
        return 400, b"{}"

    def _put(self, bk, name, data):
        self.gen += 1
        bk["live"][name] = {"generation": str(self.gen), "md5Hash": ph.md5_b64(data), "data": data}
        return {"name": name, "generation": str(self.gen), "md5Hash": ph.md5_b64(data)}


def test_cloud_run_end_to_end_against_fake_api(capsys, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "my-proj-123")
    fake = FakeGcs()
    fake.buckets["cmf-d31-old-run"] = {"live": {}, "soft": [], "policy": {"retentionDurationSeconds": "0"}}
    assert ph.cloud_main(ph.Gcs("my-proj-123", fake), "4242424242") == 0
    out = capsys.readouterr().out
    assert "::notice title=Google Cloud day 31::hard delete pause read back enabled" in out
    assert "4 restored" in out and "24 price rows" in out and "HTTP 404" in out
    assert "earlier run" in out and fake.buckets == {}         # earlier run's bucket both gone
    assert "my-proj-123" not in out and "4242424242" not in out
    patch_paths = [p for m, p, q in fake.calls if m == "PATCH"]
    assert len(patch_paths) >= 4                               # pause on, pause off, and teardown's two


def test_unsupported_pause_is_an_error_and_still_cleans_up(capsys, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "my-proj-123")
    fake = FakeGcs(pause_supported=False)
    assert ph.cloud_main(ph.Gcs("my-proj-123", fake), "77") == 1
    out = capsys.readouterr().out
    assert "::error title=Google Cloud day 31 failed::PATCH b/<bucket> -> HTTP 400" in out
    assert fake.buckets == {}


def test_compare_flags_a_lost_file_and_a_pause_that_did_not_stick():
    cloud = {"md5_mismatch_on_upload": [], "pause_enabled": False, "pause_effective_time": False,
             "live_before_restore": RESULT["live_before_restore"], "live_after": RESULT["live_after"][:-1],
             "left_soft_deleted": RESULT["left_soft_deleted"], "soft_deleted": RESULT["deleted_by_cleanup"],
             "hard_delete_times": True, "soft_deleted_read_status": [404], "restored_md5": RESULT["md5"],
             "pause_lifted": True}
    errors = ph.compare(cloud, RESULT)
    assert len(errors) == 3 and "enabled" in errors[0] and "effectiveTime" in errors[1] and "live_after" in errors[2]


def test_error_text_is_one_line_with_the_reason():
    body = json.dumps({"error": {"code": 412, "message": "Precondition\nfailed",
                                 "errors": [{"reason": "conditionNotMet"}]}}, indent=2).encode()
    assert ph.error_text(body) == "conditionNotMet: Precondition\nfailed"[:400]
    assert ph.error_text(b"<html>\n oops\n</html>") == "<html> oops </html>"


class PauseOnlyGcs(FakeGcs):
    """Refuses the pause when the retention is in the same PATCH body (HTTP 412), accepts the pause-only body."""

    def __call__(self, method, url, body, ctype):
        if method == "PATCH":
            p = json.loads(body)["softDeletePolicy"]
            if "hardDeletePause" in p and "retentionDurationSeconds" in p:
                return 412, json.dumps({"error": {"code": 412, "message": "Precondition failed",
                                                  "errors": [{"reason": "conditionNotMet"}]}}).encode()
            if "retentionDurationSeconds" not in p:
                bk = self.buckets[urllib.parse.urlparse(url).path.split("/")[-1]]
                p = {"retentionDurationSeconds": bk["policy"]["retentionDurationSeconds"], **p}
                body = json.dumps({"softDeletePolicy": p}).encode()
        return super().__call__(method, url, body, ctype)


def test_pause_only_body_is_tried_after_a_refusal(capsys, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "my-proj-123")
    fake = PauseOnlyGcs()
    assert ph.cloud_main(ph.Gcs("my-proj-123", fake), "88") == 0
    out = capsys.readouterr().out
    assert "pause-only PATCH body worked" in out and "conditionNotMet: Precondition failed" in out
    assert "hard delete pause read back enabled" in out and fake.buckets == {}


class MinDurationGcs(FakeGcs):
    """What the real service did on 2026-11-04: a pause can't be lifted for 24 h (HTTP 412 conditionNotMet with an
    'Allowed after' time), and a bucket with the pause on can't be deleted (HTTP 409)."""

    def __init__(self):
        super().__init__()
        self.now_allowed = False

    def __call__(self, method, url, body, ctype):
        path = urllib.parse.urlparse(url).path.split("/storage/v1/")[1].split("/")
        bk = self.buckets.get(path[1]) if len(path) > 1 else None
        paused = bool(bk and bk["policy"].get("hardDeletePause", {}).get("enabled"))
        if bk is not None and len(path) == 2 and paused:
            if method == "PATCH" and not json.loads(body)["softDeletePolicy"].get("hardDeletePause", {}).get(
                    "enabled", True) and not self.now_allowed:
                return 412, json.dumps({"error": {"code": 412, "errors": [{"reason": "conditionNotMet"}],
                                                  "message": "Hard Delete Pause can only be disabled after the minimum "
                                                             "duration has passed. Allowed after: "
                                                             "2026-11-06T10:00:00.123456+00:00."}}).encode()
            if method == "DELETE":
                return 409, json.dumps({"error": {"code": 409, "errors": [{"reason": "conflict"}], "message":
                                                  "The bucket you tried to delete has hard delete pause enabled."}}
                                       ).encode()
        return super().__call__(method, url, body, ctype)


def test_minimum_pause_duration_keeps_the_bucket_for_a_later_run(capsys, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "my-proj-123")
    fake = MinDurationGcs()
    assert ph.cloud_main(ph.Gcs("my-proj-123", fake), "99") == 0
    out = capsys.readouterr().out
    assert "lifting the pause refused until the minimum duration has passed (24.0 h after it took effect)" in out
    assert "one bucket kept" in out and "2026-11-06T10:00:00 UTC" in out and "::warning" not in out
    assert len(fake.buckets) == 1 and not next(iter(fake.buckets.values()))["live"]   # kept, but emptied
    fake.now_allowed = True                                                         # a day later
    assert ph.cloud_main(ph.Gcs("my-proj-123", fake), "100") == 0
    out = capsys.readouterr().out
    assert "deleted one earlier run's bucket" in out and "pause lifted" in out and fake.buckets == {}


def test_hours_between():
    assert ph.hours_between("2026-11-05T10:00:00.5Z", "2026-11-06T10:00:00") == 24.0
