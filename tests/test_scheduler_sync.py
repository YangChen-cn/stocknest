"""External job syncing runs against fake cron-job.org responses, never the real API."""
import json

import pytest
import yaml

from stockwatch.scheduler_sync import SchedulerError, apply, main


def job(mode, job_id=1, enabled=True):
    return {"jobId": job_id, "enabled": enabled,
            "extendedData": {"headers": {"Authorization": "Bearer gh-token"},
                             "body": json.dumps({"ref": "main", "inputs": {"mode": mode, "scheduled": True}})}}


class FakeAPI:
    def __init__(self, jobs):
        self.jobs = jobs
        self.calls = []
        self.some_failed = False

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "GET" and path == "/jobs":
            endpoint = "https://api.github.com/repos/Example/private/actions/workflows/daily.yml/dispatches"
            return {"jobs": [{"jobId": j["jobId"], "url": endpoint} for j in self.jobs],
                    "someFailed": self.some_failed}
        if method == "GET" and path.startswith("/jobs/"):
            job_id = int(path.rsplit("/", 1)[1])
            return {"jobDetails": next(j for j in self.jobs if j["jobId"] == job_id)}
        if method == "PATCH":
            job_id = int(path.rsplit("/", 1)[1])
            next(j for j in self.jobs if j["jobId"] == job_id).update(payload["job"])
            return {}
        if method == "PUT":
            self.jobs.append({**payload["job"], "jobId": len(self.jobs) + 1})
            return {}
        raise AssertionError(method)


PLANS = {"reports": {"INTRADAY": {"enabled": True, "time": "10:23", "days": [0, 1, 2, 3, 4]},
                     "CLOSE": {"enabled": True, "time": "18:53", "days": [0, 1, 2, 3, 4]}}}


def test_native_disables_every_external_job_and_creates_nothing():
    api = FakeAPI([job("INTRADAY", 1), job("CLOSE", 2)])
    assert apply(PLANS, "Example/private", desired="native", request=api, sleeper=lambda seconds: None) == 2
    patches = [call for call in api.calls if call[0] == "PATCH"]
    assert len(patches) == 2 and all(call[2] == {"job": {"enabled": False}} for call in patches)
    assert not any(call[0] == "PUT" for call in api.calls)


def test_native_without_jobs_is_a_noop_and_bad_trigger_rejected():
    api = FakeAPI([])
    assert apply(PLANS, "Example/private", desired="native", request=api, sleeper=lambda seconds: None) == 0
    assert not any(call[0] in ("PATCH", "PUT") for call in api.calls)
    with pytest.raises(SchedulerError):
        apply(PLANS, "Example/private", desired="hourly", request=api, sleeper=lambda seconds: None)


def test_external_mode_syncs_enabled_time_and_days():
    api = FakeAPI([job("INTRADAY", 1)])
    plans = {"reports": {"INTRADAY": {"enabled": True, "time": "09:30", "days": [1]},
                         "CLOSE": {"enabled": True, "time": "18:53", "days": [0, 1, 2, 3, 4]}}}
    assert apply(plans, "Example/private", desired="cron-job.org", request=api, sleeper=lambda seconds: None) == 2
    patch = next(call for call in api.calls if call[0] == "PATCH")
    assert patch[2]["job"]["schedule"]["hours"] == [9] and patch[2]["job"]["schedule"]["minutes"] == [30]
    assert patch[2]["job"]["schedule"]["wdays"] == [2] and patch[2]["job"]["enabled"] is True  # Tuesday only.
    put = next(call for call in api.calls if call[0] == "PUT")
    assert json.loads(put[2]["job"]["extendedData"]["body"])["inputs"] == {"mode": "CLOSE", "scheduled": True}


def test_disabled_existing_job_is_patched_off_and_missing_not_created():
    api = FakeAPI([job("CLOSE", 2)])
    plans = {"reports": {"INTRADAY": {"enabled": False, "time": "10:23", "days": [0, 1, 2, 3, 4]},
                         "CLOSE": {"enabled": False, "time": "18:53", "days": [0, 1, 2, 3, 4]}}}
    assert apply(plans, "Example/private", desired="cron-job.org", request=api, sleeper=lambda seconds: None) == 1
    assert next(call for call in api.calls if call[0] == "PATCH")[2]["job"]["enabled"] is False
    assert not any(call[0] == "PUT" for call in api.calls)


def test_main_writes_trigger_only_after_successful_apply(tmp_path, monkeypatch):
    import sys
    import stockwatch.scheduler_sync as scheduler_sync
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(PLANS, sort_keys=False), encoding="utf-8")
    # The first external switch needs the user-created template job; without it the
    # apply fails on purpose and the native schedule keeps running.
    api = FakeAPI([job("INTRADAY", 1)])
    monkeypatch.setattr(scheduler_sync, "api", api)
    monkeypatch.setattr(sys, "argv", ["scheduler_sync", "--config", str(config_path),
                                      "--repository", "Example/private", "--apply-trigger", "cron-job.org"])
    assert scheduler_sync.main() == 0
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["scheduler"] == {"trigger": "cron-job.org"}

    # Switching back disables the jobs first and only then removes the trigger.
    api2 = FakeAPI([job("INTRADAY", 1), job("CLOSE", 2)])
    monkeypatch.setattr(scheduler_sync, "api", api2)
    monkeypatch.setattr(sys, "argv", ["scheduler_sync", "--config", str(config_path),
                                      "--repository", "Example/private", "--apply-trigger", "native"])
    assert scheduler_sync.main() == 0
    assert "scheduler" not in yaml.safe_load(config_path.read_text(encoding="utf-8"))

    # A failed apply never touches the config, so the current scheduler keeps running.
    before = config_path.read_bytes()
    api3 = FakeAPI([job("INTRADAY", 1)])
    api3.some_failed = True
    monkeypatch.setattr(scheduler_sync, "api", api3)
    monkeypatch.setattr(sys, "argv", ["scheduler_sync", "--config", str(config_path),
                                      "--repository", "Example/private", "--apply-trigger", "cron-job.org"])
    assert scheduler_sync.main() == 1
    assert config_path.read_bytes() == before
