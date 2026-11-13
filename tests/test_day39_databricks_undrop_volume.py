"""Day 39 Databricks: the price-file reference, the UNDROP VOLUME SQL and ID check, picking the right row from SHOW
VOLUMES DROPPED, local pyspark reading the files the way the job does, and the REST client against a fake API
(success, a lost file after undrop, an inactive workspace). No credentials."""
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-39-price-volume-undrop"
sys.path.insert(0, str(FEATURE))
spec = importlib.util.spec_from_file_location("undrop_volume", FEATURE / "undrop_volume.py")
uv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uv)
sys.modules["undrop_volume"] = uv
spec = importlib.util.spec_from_file_location("run_cloud39", FEATURE / "run_cloud.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

HOST = "https://dbc-123.cloud.databricks.com"
VID = "6ca7be55-8f58-47a7-85ee-7a59082fd17a"
LOCAL = json.loads((FEATURE / "result.json").read_text())


def test_reference_matches_result_json():
    ref = uv.reference()
    assert {k: LOCAL[k] for k in ref} == ref
    assert (ref["file_count"], ref["rows"], ref["price_total"]) == (3, 15, "59.35")
    assert [f["price_total"] for f in ref["files"]] == ["21.55", "17.56", "20.24"]


def test_undrop_sql_only_takes_a_uuid():
    assert uv.undrop_sql(VID) == f"UNDROP VOLUME WITH ID '{VID}'"
    for bad in ("price_files", "", VID + "'; DROP SCHEMA x; --", VID.upper()):
        with pytest.raises(ValueError):
            uv.undrop_sql(bad)
    assert uv.fqn("workspace", "cmf_day39", "price_files") == "`workspace`.`cmf_day39`.`price_files`"
    with pytest.raises(ValueError):
        uv.fqn("workspace", "cmf`; --", "v")


def test_pick_dropped_matches_the_id_not_the_name():
    rows = [{"volume_name": "price_files", "volume_id": "11111111-1111-1111-1111-111111111111"},  # yesterday's
            {"volume_name": "other", "volume_id": VID},
            {"volume_name": "price_files", "volume_id": VID}]
    got = uv.pick_dropped(rows, "price_files", VID)
    assert got["volume_id"] == VID and got["same_name_dropped"] == 2
    with pytest.raises(LookupError):
        uv.pick_dropped(rows[:2], "price_files", VID)


def test_describe_comment_both_shapes():
    assert uv.describe_comment([{"name": "price_files", "comment": "c"}]) == "c"
    assert uv.describe_comment([{"info_name": "Owner", "info_value": "x"},
                                {"info_name": "Comment", "info_value": "c"}]) == "c"
    assert uv.describe_comment([{"a": 1}]) is None


def test_local_pyspark_reads_the_files_like_the_job():
    pyspark = pytest.importorskip("pyspark.sql")
    s = pyspark.SparkSession.builder.master("local[1]").appName("day39").getOrCreate()
    for f in LOCAL["files"]:
        df = s.read.option("header", True).csv(str(FEATURE / "data" / f["file"]))
        row = df.selectExpr("count(*) AS n", "CAST(sum(CAST(price AS DECIMAL(10,2))) AS STRING) AS t").first()
        assert (int(row.n), row.t) == (f["rows"], f["price_total"])


def _snap():
    files = [{**f, "spark_rows": f["rows"], "spark_price_total": f["price_total"]} for f in LOCAL["files"]]
    return {"files": files, "comment": uv.COMMENT, "grants": [[uv.GRANTEE, uv.PRIVILEGE]]}


def _cloud(**over):
    return {"engine": "Databricks serverless (Spark 4.1)", "before": _snap(), "after": _snap(),
            "readable_after_drop": False, "same_name_dropped": 1, "undropped": True, **over}


def test_compare():
    rest = {"volume_id": VID, "comment": uv.COMMENT}
    assert rc.compare(_cloud(), LOCAL, rest, VID) == []
    after = _snap()
    after["files"] = after["files"][:2]
    assert "after: files" in rc.compare(_cloud(after=after), LOCAL, rest, VID)[0]
    after = _snap()
    after["files"][0]["sha256"] = "0" * 64
    after["grants"] = []
    assert len(rc.compare(_cloud(after=after), LOCAL, rest, VID)) == 2
    assert rc.compare(_cloud(), LOCAL, {"volume_id": "x", "comment": uv.COMMENT}, VID) == [
        "the undropped volume has a different ID"]
    assert len(rc.compare(_cloud(undropped=False), LOCAL, rest, VID)) == 1


class FakeApi:
    def __init__(self, result, refuse=None):
        self.calls, self.result, self.files, self.refuse, self.submitted = [], result, {}, refuse, None

    def __call__(self, method, path, data=None, headers=None):
        self.calls.append((method, path.split("?")[0]))
        body = json.loads(data) if data and (headers or {}).get("Content-Type") == "application/json" else {}
        if path.endswith("current-metastore-assignment"):
            return 200, b'{"default_catalog_name": "workspace"}'
        if path.endswith("/scim/v2/Me"):
            return 200, b'{"userName": "someone@example.com"}'
        if method == "POST" and path.endswith("/unity-catalog/volumes"):
            return 200, json.dumps({"volume_id": VID if body["name"] == "price_files" else "r"}).encode()
        if method == "GET" and "/unity-catalog/volumes/" in path:
            return 200, json.dumps({"volume_id": VID, "comment": uv.COMMENT}).encode()
        if path.endswith("/runs/submit"):
            if self.refuse:
                return self.refuse
            self.submitted = body
            out = body["tasks"][0]["spark_python_task"]["parameters"][4] + "/result.json"
            self.files[out] = json.dumps(self.result).encode()
            return 200, b'{"run_id": 1}'
        if "/runs/get-output" in path:
            return 200, b"{}"
        if "/runs/get" in path:
            return 200, json.dumps({"state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"},
                                    "tasks": [{"run_id": 2}], "start_time": 1000, "end_time": 31000}).encode()
        if path.startswith("/api/2.0/fs/files"):
            p = unquote(path[len("/api/2.0/fs/files"):].split("?")[0])
            if method == "PUT":
                self.files[p] = data
                return 204, b""
            f = self.files.get(p)
            return (200, f) if f else (404, b"{}")
        return 200, b"{}"


def _wire(monkeypatch, fake):
    monkeypatch.setattr(rc, "_request", fake)
    monkeypatch.setattr(rc, "POLL_SECONDS", 0)
    monkeypatch.setenv("DATABRICKS_HOST", HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", "x")


def _cleaned(fake):
    return [p for m, p in fake.calls if m == "DELETE" and "/schemas/" in p]


def test_cloud_run_end_to_end_against_fake_api(monkeypatch, capsys):
    fake = FakeApi(_cloud())
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "UNDROP VOLUME WITH ID brought back all 3 files" in out and "job took 30s" in out
    assert "someone" not in out and "dbc-123" not in out
    params = fake.submitted["tasks"][0]["spark_python_task"]["parameters"]
    assert params[:4] == ["workspace", "cmf_day39", "price_files", VID]
    assert sorted(p for p in fake.files if "/price_files/" in p) == [
        f"/Volumes/workspace/cmf_day39/price_files/{f['file']}" for f in LOCAL["files"]]
    assert ("PATCH", "/api/2.1/unity-catalog/permissions/volume/workspace.cmf_day39.price_files") in fake.calls
    assert _cleaned(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day39"]


def test_lost_file_after_undrop_is_an_error(monkeypatch, capsys):
    after = _snap()
    after["files"] = after["files"][1:]
    fake = FakeApi(_cloud(after=after))
    _wire(monkeypatch, fake)
    assert rc.main() == 1
    assert "::error" in capsys.readouterr().out and len(_cleaned(fake)) == 1


def test_inactive_workspace_is_a_warning_without_ids(monkeypatch, capsys):
    fake = FakeApi({}, refuse=(404, json.dumps({"error_code": "FEATURE_DISABLED", "message": "Triggering new runs "
                                                "for organization 1234567890123 is currently disabled temporarily."}
                                               ).encode()))
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "::warning" in out and "verified locally only" in out and "1234567890123" not in out
    assert len(_cleaned(fake)) == 1
