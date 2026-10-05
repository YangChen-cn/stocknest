"""Small local controls using existing Git credentials and macOS launchd."""
from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from stockwatch.i18n import UserFacingError

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
        raise ControlError("Login startup is available only on macOS.")
    return f"gui/{os.getuid()}/{LABEL}"


def _owned(root: Path):
    path = _plist_path()
    if path.exists():
        try:
            data = plistlib.loads(path.read_bytes())
            if data.get("Label") != LABEL or data.get("WorkingDirectory") != str(root.resolve()):
                raise ValueError
        except (ValueError, OSError, plistlib.InvalidFileException):
            raise ControlError("The existing launchd entry belongs to another checkout or is invalid; inspect it in a terminal.") from None
    return path


def service_status(root: Path) -> dict:
    if platform.system() != "Darwin":
        return {"supported": False, "installed": False, "loaded": False, "running": False, "pid": None, "port_open": False}
    path = _owned(root)
    result = _command(["launchctl", "print", _target()], check=False)
    pid = re.search(r"\bpid = (\d+)", result.stdout) if result.returncode == 0 else None
    port_open = False
    try:
        with socket.create_connection(("127.0.0.1", 8501), timeout=0.3):
            port_open = True
    except OSError:
        pass
    return {"supported": True, "installed": path.exists(), "loaded": result.returncode == 0, "running": pid is not None,
            "pid": int(pid[1]) if pid else None, "port_open": port_open}


def install_service(root: Path):
    _target()
    path = _owned(root)
    root = root.resolve()
    if not (root / "app.py").is_file():
        raise ControlError("StockWatch app.py was not found.")
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    config = {"Label": LABEL, "ProgramArguments": [sys.executable, "-m", "streamlit", "run", str(root / "app.py"),
               "--server.address=127.0.0.1", "--server.port=8501", "--server.headless=true"],
              "WorkingDirectory": str(root), "RunAtLoad": True, "KeepAlive": True,
              "StandardOutPath": str(logs / "launchd.stdout.log"), "StandardErrorPath": str(logs / "launchd.stderr.log")}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_bytes(plistlib.dumps(config))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    # Register the next login only. The current Dashboard may own port 8501.
    # Loading/kickstarting here would spawn a second process from its own UI.


def stop_service(root: Path):
    _owned(root)
    if service_status(root)["loaded"]:
        _command(["launchctl", "bootout", _target()])


def start_service(root: Path):
    path = _owned(root)
    if not path.exists():
        raise ControlError("Enable login startup before starting the managed service.")
    status = service_status(root)
    if status["port_open"] and not status["running"]:
        raise ControlError("Port 8501 is already in use. Stop the manually started Dashboard before starting the managed service.")
    if not status["loaded"]:
        _command(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])
    _command(["launchctl", "kickstart", _target()])


def uninstall_service(root: Path):
    path = _owned(root)
    if service_status(root)["loaded"]:
        _command(["launchctl", "bootout", _target()])
    path.unlink(missing_ok=True)


def main(argv=None):
    import argparse
    from stockwatch.storage import ROOT
    parser = argparse.ArgumentParser(description="StockWatch macOS Dashboard service")
    parser.add_argument("action", choices=("status", "install", "start", "stop", "uninstall"))
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            print(json.dumps(service_status(args.root), ensure_ascii=False))
        else:
            {"install": install_service, "start": start_service, "stop": stop_service,
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
