"""Explicit user-initiated synchronization of the two editable data files."""
import subprocess
from pathlib import Path

from stockwatch.control import ControlError, _command, repository, gh_path
from stockwatch.i18n import UserFacingError

from stockwatch.storage import load_config, load_transactions

EDITABLE = {"config.yaml", "data/transactions.csv"}


class SyncError(UserFacingError, RuntimeError):
    pass


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise SyncError("Git unavailable or timed out. Check your terminal and network, then retry.") from None
    if check and result.returncode:
        # Raw git errors may echo credential-bearing remote URLs.
        raise SyncError("Git {command} failed. Check your Git login, repository permissions and network in a terminal.", command=args[0])
    return result


def sync(root: Path) -> str:
    load_config(root / "config.yaml")
    load_transactions(root / "data/transactions.csv")
    if Path(_git(root, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != root.resolve():
        raise SyncError("This project must be the Git repository root.")
    if _git(root, "branch", "--show-current").stdout.strip() != "main":
        raise SyncError("Switch to main before syncing portfolio data.")
    for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD"):
        marker_path = _git(root, "rev-parse", "--git-path", marker).stdout.strip()
        if (root / marker_path).exists():
            raise SyncError("Finish the existing Git operation before syncing.")
    changed = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    entries = [entry for entry in changed.split("\0") if entry]
    if any(entry[:2] not in {" M", "M ", "MM", "??", "A ", "AM"} or entry[3:] not in EDITABLE for entry in entries):
        raise SyncError("Other files have uncommitted changes. Commit or resolve them in a terminal first; nothing was discarded.")
    remote = _git(root, "remote", "get-url", "origin").stdout.strip()
    if "github.com" in remote:
        try:
            repo = repository(root)
            private = _command([gh_path(), "api", f"repos/{repo}", "--jq", ".private"], root=root).stdout.strip()
        except ControlError:
            raise SyncError("Cannot verify repository privacy. Check gh login before syncing personal data.") from None
        if private != "true":
            raise SyncError("Personal configuration can only sync to a verified private GitHub repository.")
    _git(root, "fetch", "origin", "main")
    if entries:
        _git(root, "add", "--", *sorted(EDITABLE))
        _git(root, "commit", "-m", "chore: update StockWatch portfolio configuration")
    result = _git(root, "rebase", "origin/main", check=False)
    if result.returncode:
        _git(root, "rebase", "--abort", check=False)
        raise SyncError("Remote data conflicts with your local commit. Your changes are preserved. Resolve the conflict in a terminal, then sync again.")
    # Protect against remote config modifications that fail local validation.
    load_config(root / "config.yaml")
    load_transactions(root / "data/transactions.csv")
    _git(root, "push", "origin", "main")
    return "Portfolio configuration synced with GitHub. Remote alert state has also been pulled."
