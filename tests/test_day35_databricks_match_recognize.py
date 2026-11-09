"""Day 35 Databricks: MATCH_RECOGNIZE finds temporary price reductions per SKU and shopping trips per loyalty card.
The local engine (DEFINE conditions -> one symbol per row, PATTERN -> Python regular expression, finditer = AFTER
MATCH SKIP PAST LAST ROW) gives the expected rows; result.json is current; the Spark side turns query rows into the
same result; and the cloud runner submits a serverless job, compares, warns on an inactive workspace and cleans up
(fake REST API)."""
import importlib.util
import json
from datetime import date, datetime
from pathlib import Path

FEATURE = Path(__file__).resolve().parent.parent / "features/databricks/day-35-shelf-price-patterns"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"day35_{name}", FEATURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pp = _load("price_patterns")
rc = _load("run_cloud")
RESULT = json.loads((FEATURE / "result.json").read_text())
HOST = "https://dbc-123.cloud.databricks.com"


def test_result_json_is_current():
    assert {k: v for k, v in RESULT.items() if k != "engine"} == pp.reference()


def test_headline_numbers():
    assert (RESULT["price_rows"], RESULT["skus"], RESULT["scan_rows"], RESULT["cards"]) == (126, 6, 84, 8)
    assert (RESULT["dip_count"], RESULT["trip_count"], RESULT["max_trips_per_card"]) == (4, 22, 4)
    assert RESULT["deepest_dip_pct"] == 22.3


def test_dips_match_the_pattern_and_nothing_else():
    by = {(d["sku"][:3], d["dip_no"]): d for d in RESULT["dips"]}
    assert set(by) == {("P01", 1), ("P02", 1), ("P02", 2), ("P05", 1)}   # rises-only, flat, permanent cut: none
    p05 = by[("P05", 1)]                       # stepped cut 449 -> 399 -> 349, back via 379 -> 449: one dip
    assert (p05["before_cents"], p05["promo_cents"], p05["back_cents"], p05["days"]) == (449, 349, 449, 10)
    assert (p05["start_day"], p05["cut_day"], p05["back_day"]) == ("2026-10-15", "2026-10-16", "2026-10-24")
    assert by[("P01", 1)]["days"] == 8 and by[("P02", 2)]["promo_cents"] == 499


def test_trips_split_on_gaps_over_45_minutes():
    for card in {t["card"] for t in RESULT["trips"]}:
        rows = sorted((ts for c, ts, _ in pp.scan_rows() if c == card))
        trips = [t for t in RESULT["trips"] if t["card"] == card]
        assert sum(t["scans"] for t in trips) == len(rows)
        assert [t["trip_no"] for t in trips] == list(range(1, len(trips) + 1))
        for a, b in zip(trips, trips[1:]):
            gap = datetime.fromisoformat(b["first_scan"]) - datetime.fromisoformat(a["last_scan"])
            assert gap.total_seconds() > 45 * 60


def test_sql_uses_match_recognize():
    for sql in (pp.DIPS_SQL, pp.TRIPS_SQL):
        for kw in ("MATCH_RECOGNIZE (", "PARTITION BY", "MEASURES MATCH_NUMBER()", "ONE ROW PER MATCH",
                   "AFTER MATCH SKIP PAST LAST ROW", "PATTERN (", "DEFINE", "PREV("):
            assert kw in sql
    assert "PATTERN ( strt down+ (flat | down)* up+ )" in pp.DIPS_SQL
    assert "PATTERN ( strt same_trip* )" in pp.TRIPS_SQL and "INTERVAL 45 MINUTE" in pp.TRIPS_SQL


class _Row(dict):
    def __getattr__(self, k):
        return self[k]

    def asDict(self):
        return dict(self)


class FakeSpark:
    """Stands in for a Databricks session: returns the reference rows with Spark's Python types (date, datetime)."""
    def createDataFrame(self, rows, schema):
        return self

    def selectExpr(self, *cols):
        return self

    def createOrReplaceTempView(self, name):
        pass

    def sql(self, q):
        self.q = q
        return self

    def collect(self):
        if "shelf_prices" in self.q:
            return [_Row({**d, "start_day": date.fromisoformat(d["start_day"]),
                          "cut_day": date.fromisoformat(d["cut_day"]), "back_day": date.fromisoformat(d["back_day"])})
                    for d in pp.dips_reference()]
        return [_Row({**t, "first_scan": datetime.fromisoformat(t["first_scan"]),
                      "last_scan": datetime.fromisoformat(t["last_scan"])}) for t in pp.trips_reference()]


def test_spark_rows_convert_to_the_same_result():
    assert pp.spark_result(FakeSpark()) == pp.reference()


def test_submit_payload_uses_serverless_environment_6():
    p = rc.submit_payload("/Workspace/Users/u/cmf-day35/price_patterns.py", "/Volumes/w/s/v/run")
    task = p["tasks"][0]
    assert "new_cluster" not in task and "existing_cluster_id" not in task
    assert p["environments"] == [{"environment_key": task["environment_key"], "spec": {"environment_version": "6"}}]
    assert task["spark_python_task"]["parameters"] == ["/Volumes/w/s/v/run"]


def _cloud_copy():
    return json.loads(json.dumps({**RESULT, "engine": "MATCH_RECOGNIZE on Databricks serverless"}))


def test_compare_flags_real_differences():
    assert rc.compare(_cloud_copy(), RESULT) == []
    cloud = _cloud_copy()
    cloud["trips"][0]["scans"] = 8
    errors = rc.compare(cloud, RESULT)
    assert len(errors) == 1 and errors[0].startswith("trips")


class FakeApi:
    def __init__(self, result, job_state="SUCCESS", refuse=None):
        self.calls, self.result, self.job_state, self.files, self.refuse = [], result, job_state, {}, refuse

    def __call__(self, method, path, data=None, headers=None):
        self.calls.append((method, path.split("?")[0]))
        body = json.loads(data) if data else {}
        if path.endswith("current-metastore-assignment"):
            return 200, b'{"default_catalog_name": "workspace"}'
        if path.endswith("/scim/v2/Me"):
            return 200, b'{"userName": "someone@example.com"}'
        if path.endswith("/runs/submit"):
            if self.refuse:
                return self.refuse
            self.submitted = body
            out = body["tasks"][0]["spark_python_task"]["parameters"][0] + "/result.json"
            if self.job_state == "SUCCESS":
                self.files[out] = json.dumps(self.result).encode()
            return 200, b'{"run_id": 1}'
        if "/runs/get-output" in path:
            return 200, json.dumps({"error": "boom" if self.job_state != "SUCCESS" else None}).encode()
        if "/runs/get" in path:
            return 200, json.dumps({"state": {"life_cycle_state": "TERMINATED", "result_state": self.job_state},
                                    "tasks": [{"run_id": 2}], "start_time": 1000, "end_time": 31000}).encode()
        if path.startswith("/api/2.0/fs/files"):
            from urllib.parse import unquote
            f = self.files.get(unquote(path[len("/api/2.0/fs/files"):]))
            return (200, f) if f else (404, b"{}")
        return 200, b"{}"


def _wire(monkeypatch, fake):
    monkeypatch.setattr(rc, "_request", fake)
    monkeypatch.setattr(rc, "POLL_SECONDS", 0)
    monkeypatch.setenv("DATABRICKS_HOST", HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", "x")


def _deleted(fake):
    return [p for m, p in fake.calls if m == "DELETE" and "/schemas/" in p]


def test_cloud_run_end_to_end_against_fake_api(monkeypatch, capsys):
    fake = FakeApi(_cloud_copy())
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "matched local" in out and "4 temporary price reductions" in out and "job took 30s" in out
    assert "someone" not in out
    called = [p for _, p in fake.calls]
    assert "/api/2.0/workspace/import" in called and "/api/2.0/workspace/delete" in called
    assert _deleted(fake) == ["/api/2.1/unity-catalog/schemas/workspace.cmf_day35"]
    assert fake.submitted["tasks"][0]["spark_python_task"]["parameters"] == ["/Volumes/workspace/cmf_day35/scratch/run"]


def test_failed_job_is_an_error_and_still_cleans_up(monkeypatch, capsys):
    fake = FakeApi({}, job_state="FAILED")
    _wire(monkeypatch, fake)
    assert rc.main() == 1
    assert "job ended FAILED: boom" in capsys.readouterr().out
    assert len(_deleted(fake)) == 1


def test_inactive_workspace_is_a_warning_without_ids(monkeypatch, capsys):
    fake = FakeApi({}, refuse=(404, json.dumps({"error_code": "FEATURE_DISABLED", "message": "Triggering new runs "
                                                "for organization 1234567890123 is currently disabled temporarily."}
                                               ).encode()))
    _wire(monkeypatch, fake)
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "::warning" in out and "verified locally only" in out and "1234567890123" not in out
    assert len(_deleted(fake)) == 1
