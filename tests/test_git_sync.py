import subprocess
import pytest

from stockwatch.git_sync import SyncError, sync


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def repos(tmp_path):
    remote, local, other = (tmp_path / name for name in ("remote.git", "local", "other"))
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(local)], check=True, capture_output=True)
    git(local, "config", "user.name", "StockWatch Test")
    git(local, "config", "user.email", "test@example.com")
    (local / "data").mkdir()
    (local / "config.yaml").write_text("portfolio:\n  base_currency: USD\nwatchlist: {}\n")
    (local / "data/transactions.csv").write_text("date,symbol,side,shares,price,note\n")
    (local / "data/state.json").write_text("{}\n")
    git(local, "add", ".")
    git(local, "commit", "-m", "init")
    git(local, "push", "origin", "main")
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    git(other, "config", "user.name", "Remote Test")
    git(other, "config", "user.email", "test@example.com")
    return local, other, remote


def test_sync_only_editable_files_and_pull_remote_state(repos):
    local, other, remote = repos
    (local / "data/transactions.csv").write_text("date,symbol,side,shares,price,note\n2026-09-28,XYZ,BUY,1,10,Local note\n")
    (other / "data/state.json").write_text('{"_meta":{"last_report_session":"2026-09-30"}}\n')
    git(other, "add", "data/state.json")
    git(other, "commit", "-m", "state")
    git(other, "push", "origin", "main")
    assert "synced" in sync(local)
    assert git(local, "show", "--format=", "--name-only", "HEAD") == "data/transactions.csv"
    assert "2026-09-30" in (local / "data/state.json").read_text()
    assert git(local, "status", "--porcelain") == ""
    assert git(local, "rev-parse", "HEAD") == git(remote, "rev-parse", "main")


def test_unrelated_changes_not_committed_or_discarded(repos):
    local, _, _ = repos
    (local / "secret.txt").write_text("preserve this")
    with pytest.raises(SyncError, match="Other files"):
        sync(local)
    assert (local / "secret.txt").read_text() == "preserve this"


def test_wrong_branch_rejected(repos):
    local, _, _ = repos
    git(local, "checkout", "-b", "feature")
    with pytest.raises(SyncError, match="main"):
        sync(local)


def test_conflict_aborts_and_preserves_local_commit(repos):
    local, other, _ = repos
    (local / "config.yaml").write_text("portfolio:\n  base_currency: USD\nwatchlist:\n  XYZ:\n    thesis: Local thesis\n")
    (other / "config.yaml").write_text("portfolio:\n  base_currency: USD\nwatchlist:\n  XYZ:\n    thesis: Remote thesis\n")
    git(other, "add", "config.yaml")
    git(other, "commit", "-m", "remote data")
    git(other, "push", "origin", "main")
    with pytest.raises(SyncError, match="conflicts"):
        sync(local)
    assert "Local thesis" in (local / "config.yaml").read_text()
    assert "portfolio configuration" in git(local, "log", "-1", "--format=%s")
    assert git(local, "status", "--porcelain") == ""
    assert not (local / ".git/rebase-merge").exists()


def test_dirty_state_rejected(repos):
    local, _, _ = repos
    (local / "data/state.json").write_text('{"_meta":{}}\n')
    with pytest.raises(SyncError, match="Other files"):
        sync(local)


def test_public_github_sync_rejected_before_commit(repos, monkeypatch):
    from stockwatch import git_sync
    local, _, _ = repos
    git(local, "remote", "set-url", "origin", "https://github.com/Example/public.git")
    before = git(local, "rev-parse", "HEAD")
    monkeypatch.setattr(git_sync, "_command", lambda *a, **k: subprocess.CompletedProcess(a, 0, "false\n", ""))
    with pytest.raises(SyncError, match="private GitHub"):
        sync(local)
    assert git(local, "rev-parse", "HEAD") == before
