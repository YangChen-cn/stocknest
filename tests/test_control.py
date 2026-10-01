import json
import plistlib
import subprocess

import pytest

from stockwatch import control
from stockwatch.control import ControlError
from stockwatch.daily import run
from stockwatch.storage import load_config, save_config


def test_workflow_status_and_actions_use_existing_cli(tmp_path, monkeypatch):
    calls = []
    def command(args, **options):
        calls.append(args)
        if args[0] == "git":
            output = "git@github.com:Example/stockwatch.git\n"
        elif "api" in args and "runs?" in args[-1]:
            output = json.dumps({"workflow_runs": [{"id": 42, "status": "completed", "conclusion": "success", "html_url": "https://github.com/Example/stockwatch/actions/runs/42"}]})
        elif "api" in args:
            output = json.dumps({"state": "active", "html_url": "https://github.com/Example/stockwatch/actions"})
        else:
            output = ""
        return subprocess.CompletedProcess(args, 0, output, "")
    monkeypatch.setattr(control, "_command", command)
    monkeypatch.setattr(control.shutil, "which", lambda _: "/bin/gh")
    status = control.workflow_status(tmp_path)
    assert status["repository"] == "Example/stockwatch" and status["runs"][0]["id"] == 42
    control.set_workflow_enabled(tmp_path, False)
    control.set_workflow_enabled(tmp_path, True)
    control.trigger_workflow(tmp_path, "INTRADAY", dry_run=True)
    assert any("disable" in call for call in calls) and any("enable" in call for call in calls)
    dispatch = next(call for call in calls if "run" in call)
    assert "daily.yml" in dispatch and "dry_run=true" in dispatch and "mode=INTRADAY" in dispatch
    assert all("GITHUB_TOKEN" not in str(call) for call in calls)


def test_credentials_not_echoed_and_missing_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(control.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "password-secret", "token-secret"))
    with pytest.raises(ControlError) as error:
        control._command(["gh", "api"])
    assert "secret" not in str(error.value)
    monkeypatch.setattr(control.shutil, "which", lambda _: None)
    monkeypatch.setattr(control.Path, "is_file", lambda _: False)
    with pytest.raises(ControlError, match="CLI is missing"):
        control.workflow_status(tmp_path)


def test_launchd_plist_and_only_user_requested_commands(tmp_path, monkeypatch):
    plist = tmp_path / "agents/dashboard.plist"
    (tmp_path / "app.py").write_text("# app")
    calls = []
    loaded = [False]
    def command(args, **kwargs):
        calls.append(args)
        if "print" in args:
            return subprocess.CompletedProcess(args, 0 if loaded[0] else 113, "pid = 123" if loaded[0] else "", "")
        if "bootstrap" in args:
            loaded[0] = True
        if "bootout" in args:
            loaded[0] = False
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(control, "_plist_path", lambda: plist)
    monkeypatch.setattr(control, "_command", command)
    def no_port(*args, **kwargs):
        raise OSError
    monkeypatch.setattr(control.socket, "create_connection", no_port)
    assert not control.service_status(tmp_path)["installed"]
    assert not any("bootstrap" in call for call in calls)
    control.install_service(tmp_path)
    payload = plistlib.loads(plist.read_bytes())
    assert payload["RunAtLoad"] and payload["KeepAlive"]
    assert "--server.address=127.0.0.1" in payload["ProgramArguments"]
    assert "stockwatch.daily" not in str(payload) and "TOKEN" not in str(payload)
    assert not control.service_status(tmp_path)["loaded"]
    assert not any("bootstrap" in call or "kickstart" in call for call in calls)
    control.start_service(tmp_path)
    assert control.service_status(tmp_path)["running"]
    control.stop_service(tmp_path)
    assert plist.exists() and not control.service_status(tmp_path)["running"]
    control.start_service(tmp_path)
    control.uninstall_service(tmp_path)
    assert not plist.exists() and not loaded[0]
    control.service_status(tmp_path)
    assert not plist.exists()  # Reading status never creates or enables anything.


def test_launchd_other_checkout_and_other_platform(tmp_path, monkeypatch):
    plist = tmp_path / "entry.plist"
    plist.write_bytes(plistlib.dumps({"Label": control.LABEL, "WorkingDirectory": "/another/project"}))
    monkeypatch.setattr(control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(control, "_plist_path", lambda: plist)
    with pytest.raises(ControlError, match="another checkout"):
        control.uninstall_service(tmp_path)
    assert plist.exists()
    monkeypatch.setattr(control.platform, "system", lambda: "Linux")
    assert not control.service_status(tmp_path)["supported"]
    with pytest.raises(ControlError, match="macOS"):
        control.install_service(tmp_path)


def test_email_toggle_skips_send_without_consuming_alerts(portfolio_files):
    paths = portfolio_files
    config = load_config(paths["config_path"])
    config["notifications"] = {"email_enabled": False}
    save_config(paths["config_path"], config)
    sent = []
    paths["sender"] = lambda *args: sent.append(args)
    assert run(**paths) == 0 and not sent
    state = json.loads(paths["state_path"].read_text())
    assert state["XYZ"]["below_95"]["last_notified"] is None
    assert "last_report_session" not in state.get("_meta", {})


def test_enabling_next_login_with_occupied_port_never_starts_second_process(tmp_path, monkeypatch):
    plist = tmp_path / "agents/dashboard.plist"
    (tmp_path / "app.py").write_text("# app")
    monkeypatch.setattr(control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(control, "_plist_path", lambda: plist)
    calls = []
    monkeypatch.setattr(control, "_command", lambda args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 113, "", ""))
    monkeypatch.setattr(control.socket, "create_connection", lambda *a, **kw: __import__("contextlib").nullcontext())
    control.install_service(tmp_path)
    assert plist.exists() and calls == []
    with pytest.raises(ControlError, match="Port 8501"):
        control.start_service(tmp_path)
    assert not any("bootstrap" in args or "kickstart" in args for args in calls)


def test_cloud_credentials_checks_names_only(tmp_path, monkeypatch):
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        result = "https://github.com/Example/tracker.git" if args[0] == "git" else '[{"name":"GMAIL_ADDRESS"}]'
        return subprocess.CompletedProcess(args, 0, result, "")
    monkeypatch.setattr(control, "_command", command)
    monkeypatch.setattr(control, "gh_path", lambda: "gh")
    assert control.cloud_email_status(tmp_path) == {"GMAIL_ADDRESS":True,"GMAIL_APP_PASSWORD":False,"REPORT_EMAIL":False}
    assert calls[-1][-2:] == ["--json", "name"]
