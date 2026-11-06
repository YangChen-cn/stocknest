"""Small local controls using existing Git credentials and macOS launchd."""
from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
from pathlib import Path
from urllib.parse import urlparse

from stockwatch.i18n import UserFacingError
from stockwatch.dashboard_proxy import HOST, PORT, SOCKET_NAME, IDLE_SECONDS, ready_file

LABEL = "com.stockwatch.dashboard"


class ControlError(UserFacingError, RuntimeError):
    pass


def _command(args, *, root=None, check=True):
    try:
        result = subprocess.run(args, cwd=root, text=True, capture_output=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        raise ControlError("Control command unavailable or timed out. Check the local CLI installation.") from None
    if check and result.returncode:
        # Never show raw CLI output: remotes and credential failures may contain secrets.
        raise ControlError("Control command failed. Check GitHub login, permissions or launchd in a terminal.")
    return result


def repository(root: Path) -> str:
    remote = _command(["git", "remote", "get-url", "origin"], root=root).stdout.strip()
    if remote.startswith("git@github.com:"):
        name = remote[len("git@github.com:"):]
    else:
        parsed = urlparse(remote)
        if parsed.hostname != "github.com" or parsed.scheme != "https":
            raise ControlError("A github.com origin remote is required.")
        name = parsed.path.lstrip("/")
    name = name.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise ControlError("Invalid GitHub repository remote.")
    return name


def gh_path():
    found = shutil.which("gh")
    if found:
        return found
    for path in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh"):
        if Path(path).is_file():
            return path
    raise ControlError("GitHub CLI is missing. Install gh and run gh auth login in a terminal.")


def workflow_status(root: Path) -> dict:
    gh = gh_path()
    repo = repository(root)
    try:
        workflow = json.loads(_command([gh, "api", f"repos/{repo}/actions/workflows/daily.yml"], root=root).stdout)
        runs = json.loads(_command([gh, "api", f"repos/{repo}/actions/workflows/daily.yml/runs?per_page=5"], root=root).stdout)
        return {"repository": repo, "state": workflow["state"], "url": f"https://github.com/{repo}/actions/workflows/daily.yml",
                "runs": [{key: run.get(key) for key in ("id", "status", "conclusion", "event", "created_at", "html_url")}
                         for run in runs.get("workflow_runs", [])]}
    except (KeyError, ValueError, TypeError):
        raise ControlError("GitHub returned an unexpected workflow response.") from None



def cloud_email_status(root: Path) -> dict:
    """List names only; never read or write GitHub Secret values."""
    from stockwatch.notifications.email import ENV_NAMES
    try:
        result = json.loads(_command([gh_path(), "secret", "list", "--repo", repository(root), "--json", "name"], root=root).stdout)
        names = {item["name"] for item in result}
        return {name: name in names for name in ENV_NAMES}
    except (KeyError, TypeError, ValueError):
        raise ControlError("GitHub returned an unexpected workflow response.") from None

def set_workflow_enabled(root: Path, enabled: bool):
    _command([gh_path(), "workflow", "enable" if enabled else "disable", "daily.yml", "--repo", repository(root)], root=root)


def trigger_workflow(root: Path, mode="CLOSE", *, dry_run=True, sync_only=False, lookback_days=None):
    if mode not in ("CLOSE", "INTRADAY", "WEEKLY", "MONTHLY"):
        raise ControlError("Invalid report mode.")
    if lookback_days is not None and (isinstance(lookback_days, bool) or not isinstance(lookback_days, int) or not 1 <= lookback_days <= 365):
        raise ControlError("HSBC lookback must be between 1 and 365 days.")
    _command([gh_path(), "workflow", "run", "daily.yml", "--repo", repository(root), "--ref", "main",
              "-f", f"mode={mode}", "-f", f"dry_run={str(dry_run).lower()}", "-f", "force_send=false"] + (["-f", "sync_only=true"] if sync_only else []) + (["-f", f"hsbc_lookback_days={lookback_days}"] if lookback_days is not None else []), root=root)


def _plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def _target() -> str:
    if platform.system() != "Darwin":
        raise ControlError("On-demand Dashboard is available only on macOS.")
    return f"gui/{os.getuid()}/{LABEL}"


def _installed(root: Path):
    path = _plist_path()
    if path.exists():
        try:
            data = plistlib.loads(path.read_bytes())
            if data.get("Label") != LABEL or data.get("WorkingDirectory") != str(root.resolve()):
                raise ValueError
        except (ValueError, OSError, plistlib.InvalidFileException):
            raise ControlError("The existing launchd entry belongs to another checkout or is invalid; inspect it in a terminal.") from None
        return data
    return None


def _loaded(root: Path) -> str:
    result = _command(["launchctl", "print", _target()], check=False)
    if result.returncode:
        return ""
    directory = re.search(r"^\s*working directory = (.+)$", result.stdout, re.M)
    if not directory or directory[1] != str(root.resolve()):
        raise ControlError("The existing launchd entry belongs to another checkout or is invalid; inspect it in a terminal.")
    return result.stdout


def _port_in_use() -> bool:
    result = _command(["/usr/sbin/lsof", "-nP", f"-iTCP:{PORT}", "-sTCP:LISTEN", "-Fn"], check=False)
    return any(line in {f"n{HOST}:{PORT}", f"n*:{PORT}"} for line in result.stdout.splitlines())


def service_status(root: Path) -> dict:
    if platform.system() != "Darwin":
        return {"supported": False, "state": "not_installed", "installed": False, "loaded": False,
                "pid": None, "streamlit_pid": None}
    config = _installed(root)
    service = _loaded(root)
    match = re.search(r"^\s*pid = (\d+)$", service, re.M)
    pid = int(match[1]) if match else None
    streamlit_pid = None
    if service and "stockwatch.dashboard_proxy" in service:
        state = "standby" if pid is None else "starting"
        if pid is not None:
            try:
                proxy, child = map(int, ready_file(root).read_text().split())
            except (ValueError, OSError):
                pass
            else:
                if proxy == pid and child > 0:
                    parent = _command(["/bin/ps", "-p", str(child), "-o", "ppid="], check=False)
                    if parent.returncode == 0 and parent.stdout.strip() == str(pid):
                        state, streamlit_pid = "running", child
    elif service or config and not config.get("Sockets"):
        state = "legacy"
    elif _port_in_use():
        state = "port_in_use"
    else:
        state = "stopped" if config else "not_installed"
    return {"supported": True, "state": state, "installed": config is not None, "loaded": bool(service),
            "pid": pid, "streamlit_pid": streamlit_pid}


def install_service(root: Path, *, activate=True, idle_seconds=IDLE_SECONDS):
    _target()
    previous = _installed(root)
    loaded = _loaded(root)
    root = root.resolve()
    if not (root / "app.py").is_file():
        raise ControlError("StockWatch app.py was not found.")
    if not 1 <= idle_seconds <= 86400:
        raise ControlError("Dashboard idle timeout must be between 1 and 86400 seconds.")
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    config = {"Label": LABEL, "ProgramArguments": [sys.executable, "-m", "stockwatch.dashboard_proxy",
                                                     "--idle-seconds", str(idle_seconds)],
              "WorkingDirectory": str(root), "ThrottleInterval": 2, "ExitTimeOut": 12,
              "Sockets": {SOCKET_NAME: {"SockFamily": "IPv4", "SockNodeName": HOST, "SockServiceName": str(PORT)}},
              "StandardOutPath": str(logs / "launchd.stdout.log"), "StandardErrorPath": str(logs / "launchd.stderr.log")}
    if loaded and "stockwatch.dashboard_proxy" in loaded and config == previous:
        return
    if activate:
        if loaded:
            _unload(root)
        _require_free_port()
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_plist(path, config)
    if activate:
        try:
            _bootstrap(path)
        except ControlError:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                _write_plist(path, previous)
            raise


def _write_plist(path, config):
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        plistlib.dump(config, handle)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _bootstrap(path):
    _command(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])


def _require_free_port():
    if _port_in_use():
        raise ControlError("Port 8501 is already in use. Stop the manually started Dashboard before starting the managed service.")


def _unload(root):
    _command(["launchctl", "bootout", _target()])
    deadline = time.monotonic() + 15
    while _loaded(root):
        if time.monotonic() >= deadline:
            raise ControlError("The Dashboard service is still stopping. Try again in a few seconds.")
        time.sleep(0.1)


def stop_service(root: Path):
    _installed(root)
    if _loaded(root):
        _unload(root)


def start_service(root: Path):
    config = _installed(root)
    if config is None:
        raise ControlError("Install the on-demand Dashboard service first.")
    if not config.get("Sockets"):
        install_service(root)
        return
    service = _loaded(root)
    if service and "stockwatch.dashboard_proxy" in service:
        return
    if service:
        _unload(root)
    _require_free_port()
    _bootstrap(_plist_path())


def uninstall_service(root: Path):
    config = _installed(root)
    service = _loaded(root)
    path = _plist_path()
    # A request from the Dashboard may end when launchd stops its worker.
    path.unlink(missing_ok=True)
    if service:
        try:
            _unload(root)
        except ControlError:
            if config is not None:
                _write_plist(path, config)
            raise


def main(argv=None):
    import argparse
    from stockwatch.storage import ROOT
    parser = argparse.ArgumentParser(description="StockWatch macOS Dashboard service")
    parser.add_argument("action", choices=("status", "install", "start", "stop", "uninstall"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--idle-seconds", type=int, default=IDLE_SECONDS, help="Idle timeout for install (default: 600)")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            print(json.dumps(service_status(args.root), ensure_ascii=False))
        else:
            if args.action == "install":
                install_service(args.root, idle_seconds=args.idle_seconds)
            else:
                {"start": start_service, "stop": stop_service,
                 "uninstall": uninstall_service}[args.action](args.root)
            print("Service setting updated / 服务设置已更新")
        return 0
    except ControlError as exc:
        print(exc.localized("zh-CN"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


def apply_report_schedules(root: Path, trigger: str = 'cron-job.org'):
    """Use existing gh credentials; management key stays in cloud Secrets."""
    _command([gh_path(), "workflow", "run", "scheduler.yml", "--repo", repository(root), "--ref", "main",
              "-f", f"desired_trigger={trigger}"], root=root)
